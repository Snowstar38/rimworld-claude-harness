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

A pawn-naming dialog keeps one box per name part (`names[0].current` and so on,
labelled FirstName / NickName / LastName), and each box carries its own length
cap. Text longer than the cap is REFUSED, not written: the box redraws through
`Widgets.TextField`, which would cut it back on the next frame, and the
read-back would then report a value the screen never shows.

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


def accept_landed(before_window):
    """Did the accept actually close that dialog? -> True / False / None.

    `home/dialog_text` reports `accepted` off its own call to the window's
    accept path and **hard-codes success**: live 2026-09-12 a `Dialog_NamePawn`
    printed `accepted: yes`, stayed on screen, and left Samantha named Samantha.
    Clicking the dialog's own Accept button (`python ui.py click "Accept"`)
    renamed her. So the claim is checked against the window stack: a listing
    call is free and says which window is on top now.

    None means the check itself could not be made; it is never read as success.
    """
    try:
        after = fields()
    except Exception:
        return None
    if not isinstance(after, dict):
        return None
    now = after.get("window") or None
    if now is None and not after.get("success"):
        # No dialog at all beneath the overlays: it closed.
        return True
    return now != (before_window or None)


def show(r, wrote=False, verify_accept=False):
    """One line per box, then the before/after of whatever was set.

    `wrote` is the CALLER's intent: whether text was actually sent. The
    companion answers a plain listing and a refused write with the same shape
    (success true, no `set`, a sentence in `error`), so only the caller can
    tell "nothing to report" from "your write was refused".
    """
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
        marks = []
        if f.get("label"):
            marks.append(str(f["label"]))
        if f.get("maxLength"):
            marks.append("max %s chars" % f["maxLength"])
        print("  %-28s %-24s %s"
              % (f.get("name") or "?",
                 "[%s]" % ", ".join(marks) if marks else "",
                 _show(f.get("before"))))

    s = r.get("set")
    # `Payload()` hard-codes `success: true`, so an over-long name and a
    # --field that matched nothing both come back "successful" carrying an
    # `error` and no `set`. Checked only for success, this printed the box
    # listing, printed the cap as a lower-case `note:`, and exited 0 -- so
    # `dialog.py "<long name>" --do && act.py ...` carried on as though the
    # pawn had been renamed.
    if wrote and r.get("set") is None and r.get("error"):
        print("REFUSED: %s" % r["error"])
        print("  nothing was written. The box still holds %s."
              % _show((rows[0] if rows else {}).get("before")))
        return 1
    if s:
        print("SET %s" % ("DRY RUN" if r.get("dryRun") else "DONE"))
        print("  %-28s %s -> %s"
              % (s.get("field") or "?", _show(s.get("before")), _show(s.get("after"))))
        if not verify_accept:
            print("  accepted: %s" % ("yes" if r.get("accepted") else "no"))
        else:
            landed = accept_landed(r.get("window"))
            if landed is None:
                print("  accepted: UNVERIFIED -- the companion said %s and the"
                      " window stack could not be read back. Check with `python"
                      " dialog.py`; if the box is still up, nothing was applied."
                      % ("yes" if r.get("accepted") else "no"))
            elif landed:
                print("  accepted: yes -- %s closed"
                      % (r.get("window") or "the dialog"))
            else:
                print("  accepted: NO -- the companion reported success but %s"
                      " is STILL OPEN, so the value in the box has not been"
                      " applied. Press the dialog's own button: `python ui.py"
                      " click \"Accept\"` (`python ui.py` lists what it offers)."
                      % (r.get("window") or "the dialog"))
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
    # Everything left used to be joined into the TEXT, so a typo went into the
    # box: `dialog.py "Lampblack" --do --acept` wrote `Lampblack --acept` as
    # the colony name, with --do honoured and accept never pressed.
    stray = [a for a in argv if a.startswith("-")]
    if stray:
        print("dialog.py: %s is not an option, and it would have been TYPED "
              "INTO THE BOX. The options are --field <name>, --do, --accept, "
              "--json. Nothing was typed." % ", ".join(repr(a) for a in stray))
        return 2

    try:
        rim.init()
        if argv:
            r = set_text(" ".join(argv), field=field, accept=accept, do=do)
        else:
            if accept:
                # `home/dialog_text` returns a LISTING for a null text
                # (`if (list || text == null)`) and never reaches
                # OnAcceptKeyPressed, so accept-without-text cannot be done on
                # this companion. It used to fall through to fields() and look
                # like an ordinary read, which is the one thing worse than
                # refusing.
                print("dialog.py: --accept needs the text to accept. The "
                      "companion presses accept only on a call that writes "
                      "(home/dialog_text returns a listing when text is null), "
                      "so re-send the value the box should hold:")
                print("  python dialog.py \"<the name>\" --do --accept")
                print("Nothing was typed and nothing was accepted.")
                return 2
            r = fields()
    except Exception as e:
        print("dialog.py FAILED -- NOTHING WAS TYPED.")
        print("%s: %s" % (e.__class__.__name__, e))
        return 1

    if as_json:
        print(json.dumps(r, indent=1))
        return 0
    return show(r, wrote=bool(argv), verify_accept=bool(accept and do and argv))


if __name__ == "__main__":
    sys.exit(main())
