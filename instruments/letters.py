"""The letter stack: read it, open it, DECIDE it.

  python letters.py                  # the stack: CHOICE / OPEN / INFO / ANNOUNCE,
                                     #   with each letter's age and STALE flag
  python letters.py open <id>        # left-click it; print what the dialog offers
  python letters.py decide "<text>"  # click that option in the open dialog
  python letters.py decide           # acknowledge it: click its own Close/OK
  python letters.py sweep [seconds]  # auto-dismiss stale announcements (see below)
  python letters.py dismiss <id>     # explicit right-click dismissal, game validates
  python letters.py --windows        # the raw window list, and what the scan made of it
  (add --no-hold to skip the viewer hold, below)

Letters are the game's only synchronous "you choose" channel, and the two fields
that classify one are `choices[]` -- what the letter's dialog will offer -- and
`shouldAutomaticallyOpenLetter`, the game's own "this demands attention". This
reads both, but see ACKNOWLEDGE: `choiceCount > 0` on its own is not the test it
looks like.

The letter STACK has no screen rects -- it is not a Window -- so there is
nothing to pixel-click there and nothing for `ui.py` to find.
`rimworld/open_letter` IS the left-click. What it opens is a real Window, and
that one is ui.py's, under PLAYBOOK rule 5a.

## The viewer hold

A letter opened and clicked inside the same second is a dialog nobody watching
got to read, and the dialog -- not the one-line label -- is where the event says
what it is. So `open_letter` sits VIEWER_HOLD_S with the window up before
anything clicks it, and `decide` holds too unless an open just did. Pass
`hold=False` (or `--no-hold`) in tests and anywhere nobody is watching.

## The sweep

`run.py` is the way to run time (PLAYBOOK rule 1) and it prints new letters and
dismisses nothing, so the panel fills up. `auto_dismiss()` is the one sweep, ten
real seconds of grace, and `keep_reason()` is its whole judgement. It is a
KEEP-list, not a drop-list: every test has to say yes before a letter goes, and
anything unreadable keeps the letter. See `keep_reason` for what the payload
actually proves about a letter's class.
"""
import json, sys, time, rim, ui
from pathlib import Path

TEXT_CHARS = 300
VIEWER_HOLD_S = 5.0
_last_hold = [0.0]

# --- the stream's copy of the letter stack ------------------------------------
# Which letters have already been narrated, so a stack that sits unanswered for
# an in-game day doesn't repost itself once a watch step. Keyed by
# (id, arrivalTick) rather than id alone: ids are the game's and are reused
# across a reload, and the arrival tick is what makes a repeat a repeat.
SEEN_PATH = Path(__file__).resolve().parent / "state" / "overlay-letters.json"
SEEN_KEEP = 400          # keys retained on disk; a colony makes ~20 a day
POST_PER_CALL = 8        # a first run against a full stack is a burst, not a flood

# --- auto-dismiss ------------------------------------------------------------
# How long an announcement sits on screen before the sweep takes it. Ten REAL
# seconds, asked for in BUGS.md on 2026-09-02, and a caller may override it:
# `auto_dismiss(after=30)`, or `python letters.py sweep 30`.
#
# **Real seconds, not ticks, and that is the whole reason this needs a file.**
# `ageTicks` is the game's clock, and the game is PAUSED for most of a turn --
# a letter that arrived during a five-minute paused investigation would age
# zero ticks while sitting on screen the entire time, and a letter that arrived
# at Superfast ages ~360 ticks a second. The quantity the bug is about is how
# long a viewer has been looking at the card, which only the wall clock knows.
# Every tool call is a fresh process, so first-sight goes to disk beside the
# overlay's dedup file, keyed the same way (id + arrivalTick, because ids are
# the game's and are reused across a reload).
AUTO_DISMISS_S = 10.0
SEEN_AT_PATH = Path(__file__).resolve().parent / "state" / "letter-seen-at.json"

# `pending()`'s default limit: past any real stack, so nothing is cut off.
# `auto_dismiss` rewrites the first-sight file to exactly the rows it is handed,
# so a truncated stack silently restarts the ten-second clock on every letter
# above the cap, every sweep, forever. The whole stack is the only safe input.
STACK_ALL = 10000

# Letter classes that are a DECISION whatever their buttons say. Matched as
# lowercased substrings against `type`, which upstream fills from
# `letter.GetType().FullName` (`RimBridgeServer/Source/RimWorldNotifications.cs`,
# `DescribeLetter`, the `type =` line).
#
# **"ChoiceLetter" is not the class test it reads like, in EITHER direction.**
# `Verse.StandardLetter` -- an ordinary announcement, Close and maybe Jump --
# *derives from* `RimWorld.ChoiceLetter`, which is why 13 of 13 letters on the
# live Aug 31 stack came back with a `choices[]` array (see ACKNOWLEDGE). So an
# isinstance-flavoured test would keep everything and the pile-up would be back.
# What the FULL NAME separates is the base and its named subclasses --
# `RimWorld.ChoiceLetter`, `ChoiceLetter_AcceptJoiner`, `ChoiceLetter_
# RansomDemand`, `NewQuestLetter` -- from `Verse.StandardLetter`, which does not
# contain the substring at all. A mod's own decision letter is not covered here
# and does not need to be: `is_decision` reads its BUTTONS, which is the test
# that cannot be out-of-date.
DECISION_CLASSES = ("choiceletter", "questletter")

# The option texts that are not a decision. **`choiceCount > 0` is NOT the test
# for "somebody has to choose", and believing it was cost this file its first
# afternoon.** Run live against a real stack on Aug 31: 13 letters, 13 of them
# classified CHOICE -- Eclipse, Cold snap, Psychic soothe, Muffalo revenge and
# the rest -- because RimWorld gives an ANNOUNCEMENT a dialog too, and its
# buttons come back as `choices[] = [{"text": "Close"}]` or `[{"text":
# "Close"}, {"text": "Jump to location"}]`. A gate built on the count says
# every letter is a decision, which is the mirror image of the old bug: the
# panel never clears and the pile-up is back.
#
# So the question is what the buttons SAY. Acknowledging a letter and answering
# one are different verbs. Anything outside this set -- Accept, Reject, a quest
# option, a ransom demand -- is a decision, and so is
# `shouldAutomaticallyOpenLetter` regardless of the buttons. Lowercase and
# trimmed; add to it only with a live stack in front of you.
ACKNOWLEDGE = frozenset(("close", "jump to location", "ok"))

# The options that only MOVE THE VIEW. Not an answer to anything: clicking one
# jumps the camera or opens the Quests tab and leaves the letter exactly where
# it was.
#
# **Live, 2026-09-07, turns 34-38, and it cost several turns.** `Letter_96`
# came back as CHOICE with `[View quest / Close]`, so `keep_reason` said
# "somebody has to answer it" and the sweep kept it forever; `letters.py open`
# said NO DIALOG OPENED, because the letter only jumps the camera; and
# `decide "Close"` had no dialog to click in. Three instruments, no route.
# Looking at the screen resolved it: these are **opportunity quests**, which
# have no accept and no decline -- only "Jump to item stash". "No route to
# answer" was always "nothing to answer".
#
# So a letter whose every button is in ACKNOWLEDGE or here is INFO, not CHOICE,
# and the sweep may take it. Lowercase and trimmed, like ACKNOWLEDGE, and added
# to only with a live stack in front of you.
NAVIGATE = frozenset((
    "view quest", "view quests", "open quest tab", "open the quest tab",
    "jump to item stash", "jump to stash", "jump to target", "jump to site",
    "jump to the site", "go to location", "jump"))

# Ticks per in-game hour: 60000 a day, 24 hours. Every age printed here is in
# these units, because "8.6 hours ago" is the quantity a reader reasons with.
TICKS_PER_HOUR = 2500.0

# **Older than this and the wording is history, not news.** Live 2026-09-07: an
# 8.6-hour-old "Grizzly bear hunting Longhoff" letter drove three consecutive
# Lookout passes reporting an immediate attack, about a bear that had long
# since been shot. The letter said what happened; nothing said when.
STALE_HOURS = 2.0


def _clean(s, n=TEXT_CHARS):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 3] + "..."


def pending(limit=STACK_ALL):
    """Every letter on the stack, newest first, with the fields that say what it IS.

    Kept as full rows rather than labels because the label is exactly the part
    that cannot tell a decision from an announcement: `choiceCount`/`choices[]`
    mean somebody has to choose, and `auto`
    (`shouldAutomaticallyOpenLetter`) is the game asking for the letter to be
    opened. A caller that reads only `label` is the bug this file exists for.

    **`limit` keeps the NEWEST that many and drops the OLDEST** -- upstream
    reverses `LettersListForReading` into newest-first display order and then
    `Take(limit)`s off the front. So a small limit hides the letters that have
    been standing longest, which are exactly the ones `auto_dismiss` is timing.
    The default is the whole stack for that reason; pass a small `limit` only
    when you want a peek at the top.
    """
    r = rim.game("rimworld/list_letters", {"limit": limit})
    # `currentGameTick` is the response's own clock. Keeping it lets every row
    # carry an ABSOLUTE arrival tick instead of only an age, which is the
    # difference between "this happened on day 12 at 14h" and "this was 8.3
    # hours ago" -- and an age is only true at the instant it was read.
    now = r.get("currentGameTick")
    out = []
    for l in r.get("letters") or []:
        age = l.get("ageTicks") or 0
        arrival = l.get("arrivalTick")
        if not isinstance(arrival, int):
            arrival = (now - age) if isinstance(now, int) else None
        out.append({
            "id": l.get("id"),
            "label": l.get("label"),
            "letterDef": l.get("letterDef"),
            "type": l.get("type"),
            "ageTicks": age,
            "arrivalTick": arrival,
            "choiceCount": l.get("choiceCount") or 0,
            "auto": bool(l.get("shouldAutomaticallyOpenLetter")),
            "dismissible": bool(l.get("canDismissWithRightClick")),
            "text": _clean(l.get("text")),
            "choices": [{"index": c.get("index"), "text": c.get("text"),
                         "disabled": bool(c.get("disabled")),
                         "disabledReason": c.get("disabledReason")}
                        for c in (l.get("choices") or [])],
        })
    return out


def new_letters(before_ids, limit=40):
    """Full rows for the ids that were not on the stack before.

    `run.py` snapshots ids before it runs time and stops on a new one; this is
    what that id is worth once you have it.
    """
    before = set(before_ids or ())
    return [l for l in pending(limit) if l["id"] not in before]


def _opt(c):
    return (c.get("text") or "").strip().lower()


def real_choices(row):
    """The options that are an actual choice -- not Close/OK, not a Jump.

    Takes either a `pending()` row or a raw `list_letters` letter, so the one
    predicate serves every caller and nobody re-implements it inline.
    """
    return [c for c in (row.get("choices") or [])
            if _opt(c) not in ACKNOWLEDGE and _opt(c) not in NAVIGATE]


def navigate_only(row):
    """True when every button on this letter merely closes it or moves the view.

    The opportunity-quest shape -- see NAVIGATE. There is no answer to give, so
    this is INFO: it may be read, it may be jumped to, and it may be swept.
    A letter with no reported buttons is NOT this (`choices` is null for
    anything that is not a ChoiceLetter, so its options are unread, not
    absent), and neither is a plain `[Close]` announcement, which is already
    handled and is not a quest.
    """
    ch = row.get("choices") or []
    if not ch or real_choices(row):
        return False
    return any(_opt(c) in NAVIGATE for c in ch)


def is_quest(row):
    return "quest" in ("%s %s" % (row.get("type") or "",
                                  row.get("letterDef") or "")).lower()


def opportunity_note(row):
    """The one line to print for a letter that has nothing to answer, or None.

    Named for what the screen showed on 2026-09-07: an *opportunity* quest with
    no accept and no decline. The line says what it is and what clears it,
    because the three instruments that met this each said something true and
    none of them said that.
    """
    if not navigate_only(row):
        return None
    buttons = ", ".join(repr(c.get("text")) for c in (row.get("choices") or []))
    return ("%snothing to answer -- its only options are %s, which jump the"
            " view or close the card. No dialog opens and no decision is"
            " pending. `python letters.py dismiss %s` clears it off the stack."
            % ("opportunity quest: " if is_quest(row) else "",
               buttons, row.get("id")))


def age_hours(row):
    return (row.get("ageTicks") or 0) / TICKS_PER_HOUR


def is_stale(row, hours=STALE_HOURS):
    """Old enough that the wording is history. See STALE_HOURS."""
    return age_hours(row) >= hours


def is_decision(row):
    """True if this letter is somebody's to ANSWER, not merely to read.

    The single gate: `watch.py` dismisses what this calls false, and everything
    else says DECIDE IT. See ACKNOWLEDGE for why it is not a count, and
    NAVIGATE for why `auto` alone is not one either -- an opportunity quest
    sets `shouldAutomaticallyOpenLetter` and still offers nothing to answer.
    """
    if navigate_only(row):
        return False
    return bool(row.get("auto") or row.get("shouldAutomaticallyOpenLetter")
                or real_choices(row))


def hold_for(hold=True):
    """Sit still with the dialog up so the stream can read it. [M] Aug 31.

    M asked for this for the viewers, not for the game. The guard is
    only wide enough to catch a back-to-back open->decide holding twice for one
    letter; a decide that arrives later is a separate beat on screen and gets
    its own five seconds. Returns the seconds actually spent.
    """
    if not hold or time.time() - _last_hold[0] < VIEWER_HOLD_S * 2:
        return 0.0
    time.sleep(VIEWER_HOLD_S)
    _last_hold[0] = time.time()
    return VIEWER_HOLD_S


# Window types that are never "the dialog swallowing map input".
#
# `LudeonTK.EditWindow_Log` joined this list on 2026-09-02: `letters.py open`
# reported the debug log as the window that had opened when the QUESTS TAB was
# what actually opened. An EditWindow is a non-modal developer editor -- it
# absorbs nothing, pauses nothing, and can sit open for a whole session -- so
# one left open silently renamed every dialog this file reports, including the
# read that decides whether a modal is eating clicks. Prefixes, so
# EditWindow_Log, EditWindow_DebugInspector and the rest go together.
IGNORE_WINDOWS = ("Verse.ImmediateWindow", "RimWorld.MainTabWindow",
                  "LudeonTK.EditWindow", "Verse.EditWindow")

# `dialog_window()` has THREE answers and collapsing two of them is what makes a
# caller say the screen is clear when it does not know: a type string (a dialog
# is up), None (the window list was READ and holds no modal), UNKNOWN (the
# bridge did not answer, so nothing was read). UNKNOWN is a str subclass, so it
# prints and is truthy -- every "is something in the way?" test reads it as YES
# -- and it is a singleton, so `w is UNKNOWN` is the test. A candidate window
# with no reported type comes back as UNNAMED, never as "": an empty string is
# falsy in one caller and truthy in the next, which is the same collapse again.
class _Unknown(str):
    pass


UNKNOWN = _Unknown("UNVERIFIED: the bridge did not answer")
UNNAMED = "a dialog (type not reported)"


def windows_ranked(s):
    """Candidate modal windows from a `get_ui_state`, best first. Pure.

    Ranking, added 2026-09-02 with IGNORE_WINDOWS above: a window that says
    `absorbInputAroundWindow` or `forcePause` IS the one eating input, and it
    outranks a window that merely sits on the `Dialog` layer. Among equals the
    LAST one in the list wins -- `Find.WindowStack.Windows` is bottom-to-top, so
    the last entry is the topmost and the most recently opened, which is the one
    a click would actually land on.
    """
    cands = []
    for i, w in enumerate(s.get("windows") or []):
        t = w.get("type") or ""
        if t.startswith(IGNORE_WINDOWS):
            continue
        modal = bool(w.get("absorbInputAroundWindow")) or bool(w.get("forcePause"))
        if not (modal or w.get("layer") == "Dialog"):
            continue
        cands.append((1 if modal else 0, i, w))
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return [w for _, _, w in cands]


def dialog_window():
    """The modal dialog absorbing input. Read from the window LIST.

    Three answers, never two -- see UNKNOWN above: the window's `type` if a
    modal is up (UNNAMED if the game names no type), None if the list was read
    and holds none, UNKNOWN if the bridge refused or did not answer. Callers
    must treat UNKNOWN as NOT clear; only None is a read that says clear.

    **Not `move.blocking_window()`, and this is the sharpest edge found here.**
    That one reads `uiState.focusedWindowType`, and live on Aug 31 the field
    said `Verse.ImmediateWindow` while a letter dialog
    (`RimWorld.Dialog_NodeTreeWithFactionInfo`) -- `layer: Dialog`,
    `absorbInputAroundWindow: true`, `forcePause: true` -- sat open
    underneath it. The letter stack is itself a Super-layer
    ImmediateWindow at the top right of the screen and it takes the focus
    field, so which one you see is a coin toss: the same code reported the
    dialog for `Eclipse` and NO DIALOG for `Animal disease: Flu` minutes apart,
    with an identical dialog open both times.

    `get_ui_state` returns the whole `windows[]` array. Ask it what is open
    instead of asking what is focused. The same trap is already written down
    from the other direction in `notes/rimworld-from-claude-code.md`: "**
    `move.blocking_window()` returns None for this dialog** -- it does not see
    it", about `Dialog_Trade`. Two sightings, one cause.

    Three tests, widening: the window list, then
    `nonImmediateDialogWindowOpen`, then the old focus test last -- so this is
    a strict SUPERSET of `move.blocking_window()` and can never miss something
    the focus field would have caught (a float menu, say).

    2026-09-02: the window-list test is now RANKED and filtered rather than
    first-match. See `IGNORE_WINDOWS` and `windows_ranked` above -- an open
    debug log used to win the scan and get reported as the dialog. `python
    letters.py --windows` prints the raw list the game reports, which is the
    only way to see this from outside.
    """
    try:
        s = rim.game("rimworld/get_ui_state", {}, strict=False)
    except Exception:
        return UNKNOWN
    # A refusal is a dict with `success: false` and none of the keys asked for,
    # and a split or truncated reply arrives as a bare string. Both read as an
    # empty screen if you go straight to `.get`.
    if not isinstance(s, dict) or s.get("success") is False:
        return UNKNOWN
    if not any(k in s for k in ("windows", "focusedWindowType",
                                "nonImmediateDialogWindowOpen")):
        return UNKNOWN
    wins = s.get("windows") or []
    for w in windows_ranked(s):
        return w.get("type") or UNNAMED
    f = s.get("focusedWindowType") or ""
    named = f if f and not f.startswith(IGNORE_WINDOWS) else ""
    if s.get("nonImmediateDialogWindowOpen"):
        # Something modal is up even though the list did not name it. Say so:
        # a vague blocker is the safe answer, `None` is the dangerous one.
        #
        # One exception, measured live 2026-09-02 with the debug log open:
        # `nonImmediateDialogWindowOpen` counts EVERY non-immediate window on
        # the Dialog layer, and `LudeonTK.EditWindow_Log` is one. So filtering
        # the log out of the LIST above and then falling through to this flag
        # just renames the same wrong answer "a dialog (type not reported)" --
        # which is worse, because now nothing says which window it was. When the
        # list is readable and the only Dialog-layer windows in it are ones we
        # deliberately ignore, the flag is EXPLAINED and the honest answer is
        # None. (If a real modal were open, `windows_ranked` would have named it
        # and we would never reach here.)
        explained = [w for w in wins
                     if (w.get("type") or "").startswith(IGNORE_WINDOWS)
                     and (w.get("layer") == "Dialog"
                          or w.get("absorbInputAroundWindow"))]
        if wins and explained and not named:
            return None
        return named or UNNAMED
    return named or None


class Option(tuple):
    """One button: a plain `(text, actionable)` pair, plus the payload's own.

    Unpacks as the two-tuple every caller already reads, and carries `index`,
    `disabled` and `disabledReason` as attributes for anyone who wants what the
    screen cannot say. The scrape can only ever fill the first two.
    """

    def __new__(cls, text, actionable, index=None, disabled=False, reason=None):
        o = tuple.__new__(cls, (text, bool(actionable)))
        o.text = text
        o.actionable = bool(actionable)
        o.index = index
        o.disabled = bool(disabled)
        o.disabledReason = reason
        return o


def options(letter=None):
    """What the open dialog offers, in order: [(text, actionable), ...].

    **The payload first.** `rimworld/list_letters` already reports the dialog's
    own buttons -- `choices[].index/.text/.disabled/.disabledReason` -- and
    `pending()` copies them onto every row, so pass the letter (a `pending()`
    row, a raw `list_letters` letter, or an id) and nothing is scraped off the
    screen. That keeps `index` and the real `disabled` flag, which the scrape
    loses, and costs no `get_ui_layout`.

    The scrape is the fallback for the case it was written for: a dialog whose
    letter reports no choices at all (upstream builds `choices` as *null* for
    anything that is not a `ChoiceLetter`), or a letter that is no longer on the
    stack to be looked up.

    Target ids are deliberately NOT returned by either path. They regenerate on
    every `get_ui_layout`, so an id read here is dead before anyone clicks it
    (PLAYBOOK 5a). Read the text, then call `decide(text)`, which fetches and
    clicks in one breath. From the scrape, `actionable` is the raw flag and it
    lies downward: a RimWorld button is an inert label with an invisible button
    under it, so a False there does not mean the line cannot be clicked --
    `ui.py` pairs them. From the payload it is `not disabled`, which is the
    game's own answer.
    """
    row = letter
    if letter is not None and not isinstance(letter, dict):
        row = next((l for l in pending() if str(l.get("id")) == str(letter)), None)
    choices = (row or {}).get("choices") or []
    if choices:
        return [Option(c.get("text"), not c.get("disabled"), c.get("index"),
                       c.get("disabled"), c.get("disabledReason"))
                for c in sorted(choices, key=lambda c: (c.get("index") is None,
                                                        c.get("index") or 0))]
    return _scrape_options()


def _scrape_options():
    """The buttons as `get_ui_layout` draws them, top of the screen down."""
    rows, seen = [], set()
    for s in rim.game("rimworld/get_ui_layout", {}).get("surfaces") or []:
        t = s.get("type") or ""
        if t.startswith(IGNORE_WINDOWS):
            continue
        for e in s.get("elements") or []:
            lab = (e.get("label") or "").strip()
            if lab:
                rows.append(((e.get("screenRect") or {}).get("y", 0), lab,
                             bool(e.get("actionable"))))
    rows.sort(key=lambda r: r[0])
    out = []
    for _, lab, act in rows:
        if lab not in seen:
            seen.add(lab)
            out.append(Option(lab, act))
    return out


def open_letter(letter_id, hold=True, poll=0.3, timeout=4.0, letter=None):
    """Left-click a letter. Returns {window, options}, or None.

    `rimworld/open_letter` reports success for a letter that opens no window at
    all -- some only jump the camera to their look target -- so success is not
    evidence and `dialog_window()` is.

    None covers two different things and the printed line says which: nothing
    opened, or the bridge never answered and what is on screen is UNVERIFIED.
    The second is not a clear screen; do not click into the map after it.

    **It is polled, not slept at.** A single 0.6s settle said NO DIALOG for a
    dialog that was on its way up (Aug 31), and a false negative here is the
    worst thing this file can print: it tells you no decision is waiting when
    one is. Wait for the window up to `timeout` before saying it never came.
    """
    rim.game("rimworld/open_letter", {"letterId": letter_id})
    deadline, win = time.time() + timeout, None
    while True:
        time.sleep(poll)
        win = dialog_window()
        # Keep polling through an unreadable bridge as well as through a slow
        # dialog: only a named window ends the wait early.
        if win is not None and win is not UNKNOWN:
            break
        if time.time() > deadline:
            break
    if win is UNKNOWN:
        print("   ~~ open_letter(%s): UNVERIFIED -- the bridge did not answer in"
              " %.1fs, so whether a dialog opened is UNKNOWN. Nothing was read;"
              " do not treat the screen as clear. `python letters.py --windows`"
              " is the check." % (letter_id, timeout))
        return None
    if not win:
        # When the row is known, say WHICH kind of nothing this is. An
        # opportunity quest reaching this line used to read as a failure; it is
        # the letter working as designed and having nothing to answer.
        note = opportunity_note(letter) if isinstance(letter, dict) else None
        print("   ~~ open_letter(%s): NO DIALOG OPENED in %.1fs. Some letters"
              " only jump the camera to their look target. The letter is still"
              " on the stack; nothing is waiting on a click. To explicitly clear "
              "it, use `python letters.py dismiss %s` (the game can refuse).%s"
              % (letter_id, timeout, letter_id, ("\n      " + note) if note else ""))
        return None
    hold_for(hold)
    # The letter's own `choices[]` say what the dialog offers; pass `letter` (a
    # `pending()` row) if you already have it and even the lookup is saved.
    return {"window": win, "options": options(letter if letter is not None
                                              else letter_id)}


def _norm(s):
    return " ".join((s or "").split()).lower()


def _pick(opts, option_text):
    """The one option `option_text` names, or None after printing the refusal.

    Exact (case-insensitive, whitespace-collapsed) beats substring, and a
    substring only counts when exactly one option contains it. Raises when
    nothing matches: a click that lands on the wrong button is worse than a
    traceback, and the raise carries every option the dialog offers.
    """
    want = _norm(option_text)
    hits = ([o for o in opts if _norm(o.text) == want]
            or [o for o in opts if want and want in _norm(o.text)])
    uniq, seen = [], set()
    for o in hits:
        if _norm(o.text) not in seen:
            seen.add(_norm(o.text))
            uniq.append(o)
    if not uniq:
        raise LookupError(
            "decide(%r): no option on this dialog carries that text. It offers:"
            " %s" % (option_text, [o.text for o in opts]))
    if len(uniq) > 1:
        print("   ~~ decide: %r matches %d of the dialog's options -- %s."
              " Nothing clicked; pass the full text of the one you mean."
              % (option_text, len(uniq),
                 ", ".join(repr(o.text) for o in uniq)))
        return None
    pick = uniq[0]
    if pick.disabled:
        print("   ~~ decide: %r is DISABLED%s. Nothing clicked."
              % (pick.text, (" -- %s" % pick.disabledReason)
                 if pick.disabledReason else ""))
        return None
    return pick


def decide(option_text=None, hold=True, settle=0.8, letter=None):
    """Answer the open dialog: click `option_text`, or acknowledge it bare.

    **Matched against the dialog's own options, not against every label on
    screen**: case-insensitive EXACT first, then a substring fall-back only if
    exactly one option contains the text. Anything else is a refusal that
    clicks nothing -- no match raises with the options listed, ambiguity and a
    disabled option print the candidates and return False. It never guesses,
    because accept on a quest letter is a signed contract and the failure mode
    of guessing is accepting the thing you meant to refuse. A letter whose only
    button is Close is what `decide()` with no argument is for.

    Pass `letter` (a `pending()` row or an id) for the game's own `disabled`
    flag; without it the options are scraped off the screen, which knows the
    texts but not which of them the game has greyed out.

    **Bare `decide()` clicks the dialog's own Close/OK button; it does not just
    press accept.** Live, Aug 31: `rimworld/press_accept` on the Eclipse
    letter's `RimWorld.Dialog_NodeTreeWithFactionInfo` returned `success: true`
    with `changed: false`, `windowCountDelta: 0` and the message "UI state did
    not change" -- the window reports `closeOnAccept: false` and `closeOnCancel:
    false`, so a letter dialog ignores the accept and cancel keys entirely.
    Clicking `Close` closed it and took the letter off the stack. A success
    string is not evidence; the window count is. `press_accept` is kept only as
    the last resort for a dialog that shows no acknowledging button at all.
    """
    win = dialog_window()
    if win is UNKNOWN:
        print("   ~~ decide: UNVERIFIED -- the bridge did not answer, so whether"
              " a dialog is open is UNKNOWN. Nothing clicked; the screen is not"
              " known to be clear.")
        return False
    if win is None:
        # Refuse rather than click into the map UI: with no dialog up, a bare
        # decide() would go hunting for a "Close" among the main tabs.
        print("   ~~ decide: no dialog is open. Nothing to answer -- open a"
              " letter first (`python letters.py`).")
        return False
    hold_for(hold)
    if option_text is not None:
        pick = _pick(options(letter), option_text)
        if pick is None:
            return False
        ui.click((pick.text or "").strip(), exact=True)
    else:
        ack = next((lab for lab, _ in options()
                    if lab.strip().lower() in ACKNOWLEDGE), None)
        if ack:
            ui.click(ack)
        else:
            rim.game("rimworld/press_accept", {}, strict=False)
            time.sleep(settle)
    left = dialog_window()
    if left is UNKNOWN:
        print("   ~~ decide: UNVERIFIED -- the bridge did not answer after the"
              " click, so whether the dialog closed is UNKNOWN. The letter may"
              " still be on the stack; `python letters.py` says.")
        return False
    if left:
        print("   ~~ decide: %s is STILL OPEN -- whatever that pressed did not"
              " close the dialog, and the letter is still on the stack. Read"
              " options() again: a second box may have stacked on top of the"
              " first, or the button is named something else." % left)
    return left is None


def dismiss(letter_id):
    """Right-click a letter off the stack. Announcements only -- see watch.py."""
    return rim.game("rimworld/dismiss_letter", {"letterId": letter_id})


# --- auto-dismiss -------------------------------------------------------------

def _seen_at_load():
    """{key: first-sight epoch}. Never raises; a lost file costs one sweep."""
    try:
        data = json.loads(SEEN_AT_PATH.read_text(encoding="utf-8"))
        got = data.get("firstSeen") if isinstance(data, dict) else None
        return {str(k): float(v) for k, v in (got or {}).items()}
    except Exception:
        return {}


def _seen_at_save(d):
    try:
        SEEN_AT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN_AT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"firstSeen": d}), encoding="utf-8")
        tmp.replace(SEEN_AT_PATH)
    except Exception:
        pass


def _key(row):
    return "%s|%s" % (row.get("id"), row.get("arrivalTick"))


def keep_reason(row):
    """Why this letter must STAY on the stack, or None if it may be swept.

    A keep-list, deliberately, and every branch returns the sentence that will
    be printed if anybody asks why the panel is still full. **Dismissing a
    choice letter silently is worse than clutter** -- right-clicking one ANSWERS
    it by throwing it away, and that is how every offer the colony got on days
    26-33 was refused at the speed of a print statement (CHRONICLE). So the
    order below is cheapest-and-most-certain first, and an unreadable row is a
    keep, never a shrug.

    The tests, and what each one actually proves:

    1. **No id.** Nothing to send to `dismiss_letter` and nothing to name in the
       log. Not a judgement about the letter at all.
    2. **`is_decision`** -- the single existing gate, and still the one that
       matters: `shouldAutomaticallyOpenLetter` is the game's own "this demands
       attention", and any button outside ACKNOWLEDGE is somebody's to answer.
       It reads BUTTONS, so a mod's decision letter is covered without anyone
       maintaining a list.
    3. **The class was not reported.** `type` is `letter.GetType().FullName`
       upstream and is present on every letter the bridge has ever returned. A
       row without it came from somewhere this file does not understand.
    4. **The class NAMES a decision** -- see DECISION_CLASSES. This is belt and
       braces over 2: a quest letter whose options happened to read as Close
       still never goes.
    5. **No buttons reported at all.** This is the sharp one and it is the
       reason the sweep can be trusted. Upstream builds `choices` as *null*
       unless the letter `is ChoiceLetter` (`RimWorldNotifications.cs`,
       `DescribeLetter`: `var choices = choiceLetter == null ? null : ...`),
       and `pending()` turns null into `[]`. So an EMPTY choices array does not
       mean "this letter has no buttons" -- it means **the payload could not
       tell us what its buttons are**, because it is some other Letter subclass
       entirely. Unread options are not absent options. Keep it.
    6. **The game says it cannot be right-clicked away.** `CanDismissWithRightClick`
       straight off the Letter; `dismiss_letter` refuses these anyway and
       answers with `success: false`, so this only saves the round trip and the
       scary-looking error.
    """
    if not row.get("id"):
        return "no id in the row -- nothing to dismiss and nothing to name"
    if is_decision(row):
        return ("somebody has to answer it" if real_choices(row)
                else "the game asked for this one to be opened"
                     " (shouldAutomaticallyOpenLetter)")
    t = row.get("type") or ""
    if not t:
        return "the letter's class was not reported, so what it is is unknown"
    low = t.lower()
    # ...unless every button on it merely jumps the view. `NewQuestLetter`
    # matches `questletter` here, and an OPPORTUNITY quest is a NewQuestLetter
    # with no accept and no decline -- so this belt-and-braces test was the last
    # thing pinning `Letter_96` to the stack forever. A letter nobody can answer
    # cannot be answered by accident, which is the whole risk this list guards.
    if not navigate_only(row):
        for mark in DECISION_CLASSES:
            if mark in low:
                return "class %s is a decision letter" % t
    if not (row.get("choices") or []):
        return ("no buttons reported -- `choices` is null for anything that is"
                " not a ChoiceLetter, so its options are unread, not absent")
    if not (row.get("dismissible") or row.get("canDismissWithRightClick")):
        return "the game says it cannot be dismissed with a right-click"
    return None


def auto_dismiss(rows=None, after=AUTO_DISMISS_S, now=None, verbose=True):
    """Sweep announcements that have sat on screen `after` real seconds.

    Returns `[(row, seconds_on_screen)]` for what went. Pass `after=` to change
    the ten seconds; nothing else in this file hard-codes the number.

    **`rows` must be the WHOLE stack** (the default fetches it, and `pending()`
    is uncapped for this reason -- see STACK_ALL), because the first-sight file
    is rewritten to exactly what is standing: that is what prunes it, and a
    filtered or truncated subset resets the clock on everything left out, every
    sweep. `new_letters()` output is the wrong shape for this.

    **A letter is never thrown away before it has been reported.** Three things
    guarantee it, in order:

    * nothing is dismissed on the sweep that FIRST sees it -- the earliest a
      letter can go is the second sweep, `after` seconds later, and by then
      `watch.py` or `run.py` has printed it at least once;
    * `post_new_to_overlay` is called here too (deduped on disk, so this is a
      no-op for anything the feed already has) -- the stream sees the card
      before the panel loses it, and it is cheap insurance for the one path
      that prints nothing, `run.until(verbose=False)` inside `move.goto`;
    * the dismissal LINE itself names the letter, so the throwing-away is in
      the same stdout Hands reads and the same log a later reader reads. That
      line is the point: a stack that quietly empties itself is the bug this
      whole file was written about, from the other end.

    Verbose is the honest default. A silent sweep is exactly the thing BUGS.md
    is asking to be able to see.
    """
    if rows is None:
        rows = pending()
    now = time.time() if now is None else now
    post_new_to_overlay(rows)
    # `standing` is rebuilt from the live stack and then written back, so a
    # letter that has left the panel drops out of the file by construction --
    # no expiry rule to get wrong, and no unbounded growth.
    known, standing, gone = _seen_at_load(), {}, []
    for r in rows:
        key = _key(r)
        first = known.get(key)
        if not isinstance(first, float) or first > now:
            # First sight, or a clock that went backwards. Start the ten
            # seconds now and let this letter through untouched.
            first = now
        standing[key] = first
        why = keep_reason(r)
        onscreen = now - first
        if why is not None or onscreen < after:
            continue
        try:
            # A success string is not evidence (PLAYBOOK rule 4). Upstream
            # re-reads the stack and answers `dismissed: <letter is gone>`; if
            # it says false the letter is still up, whatever `success` said.
            got = dismiss(r["id"])
            if isinstance(got, dict) and got.get("dismissed") is False:
                raise RuntimeError("the bridge reports the letter is still on"
                                   " the stack (dismissed: false)")
        except Exception as e:
            if verbose:
                print("   ~~ auto-dismiss: could not dismiss %s (%s) -- it stays"
                      " on the stack." % (r.get("label") or r["id"], str(e)[:80]))
            continue
        standing.pop(key, None)
        gone.append((r, onscreen))
        if verbose:
            print("   -- AUTO-DISMISSED after %.0fs on screen: %s"
                  % (onscreen, (r.get("label") or "?")))
            print("      why it was safe: %s; buttons were [%s]; id=%s"
                  % (r.get("type") or "?",
                     ", ".join((c.get("text") or "?") for c in (r.get("choices") or [])),
                     r.get("id")))
    _seen_at_save(standing)
    return gone


# --- the stream side ----------------------------------------------------------
#
# Letters are the only channel where the game says, in its own words, that
# something happened -- and until 2026-09-01 they went to stdout and nowhere
# else, so the overlay showed errata's commentary with no sight of the events it
# was commenting on. These push the LABEL (never the body: a letter's text is
# three paragraphs and the card is one line) with the arrival tick and a colour.

# Matched as substrings, lowercased, against `letterDef` and `type` together,
# BAD first. RimWorld's own letter defs are the vocabulary: ThreatBig /
# ThreatSmall / NegativeEvent / Death on one side, PositiveEvent /
# AcceptJoiner / AcceptVisitors on the other, NeutralEvent in the middle. Only
# `ThreatBig` has actually been seen in the saves on this machine, so the rest
# is matched loosely on purpose -- a mod's `ThreatBig_Mechanoid` or a
# `ChoiceLetter_AcceptJoiner` should land in the right bucket without anyone
# maintaining a list, and anything unrecognised is neutral, which is safe.
_TONE_BAD = ("threat", "negative", "death", "died", "danger", "raid", "attack",
             "hostil", "bad", "injur", "disease", "fail")
_TONE_GOOD = ("positive", "good", "join", "accept", "birth", "reward", "gift",
              "recruit", "success")


def tone(row):
    """good | bad | neutral for one letter row. Never raises, never guesses wildly."""
    try:
        blob = ("%s %s" % (row.get("letterDef") or "", row.get("type") or "")).lower()
    except Exception:
        return "neutral"
    for word in _TONE_BAD:
        if word in blob:
            return "bad"
    for word in _TONE_GOOD:
        if word in blob:
            return "good"
    return "neutral"


def _seen_load():
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        keys = data.get("seen") if isinstance(data, dict) else data
        return [str(k) for k in keys] if isinstance(keys, list) else []
    except Exception:
        return []


def _seen_save(keys):
    try:
        SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seen": keys[-SEEN_KEEP:]}), encoding="utf-8")
        tmp.replace(SEEN_PATH)
    except Exception:
        pass


def post_new_to_overlay(rows):
    """Post letters the stream hasn't shown yet. Returns how many were sent.

    **Wrapped in a bare except on purpose, and called before anything dismisses
    anything.** `watch.py` right-clicks announcements off the stack the moment
    it prints them, so a letter that isn't narrated here is gone -- and the one
    thing this must never do is turn a stream problem into a play problem. A
    missing `overlay_client`, a dead server, a malformed row: zero posted, no
    exception, the turn continues.

    Dedup lives on disk (`state\\overlay-letters.json`) rather than in memory
    because every tool call is a fresh process: an in-memory set would repost
    the whole stack once a step.
    """
    try:
        import overlay_client as ov
    except Exception:
        return 0
    try:
        rows = [r for r in (rows or []) if r and (r.get("label") or "").strip()]
        seen = _seen_load()
        known = set(seen)
        fresh = []
        for r in rows:
            key = "%s|%s" % (r.get("id"), r.get("arrivalTick"))
            if key not in known:
                known.add(key)
                fresh.append((r, key))
        # Oldest first so the feed reads in the order the colony lived it, and
        # capped so the first run against a stack that has been piling up for
        # six in-game days is a burst, not a flood. **Everything fresh is
        # recorded as seen, including what the cap dropped** -- otherwise the
        # overflow stays "new" forever and resurfaces on some later step where
        # it happens to fit, putting day-29 news on the feed on day 34.
        fresh.sort(key=lambda rk: rk[0].get("arrivalTick") or 0)
        to_post = fresh[-POST_PER_CALL:] if POST_PER_CALL > 0 else []
        # One ordered batch, not a post each: separate posts race each other on
        # their own threads and land shuffled, and the feed is a timeline.
        ov.post_letters([(r.get("label"), r.get("arrivalTick"), tone(r))
                         for r, _ in to_post])
        seen.extend(key for _, key in fresh)
        if fresh:
            _seen_save(seen)
        return len(to_post)
    except Exception:
        return 0


def _kind(row):
    if navigate_only(row):
        return "INFO    "
    if real_choices(row):
        return "CHOICE  "
    return "OPEN    " if is_decision(row) else "ANNOUNCE"


def show(rows):
    """The stack. Announcements get one line; a decision gets its text and
    every button, because that is the row you are about to have to answer.

    The AGE is on every row and `STALE` is on every row older than
    STALE_HOURS, because this line is what the Lookout and Hands read and a
    letter's wording is a report of the moment it arrived, not of now."""
    for l in rows:
        hours = age_hours(l)
        print("%s %-40s %5.1fh %s id=%s%s"
              % (_kind(l), (l["label"] or "")[:40], hours,
                 "STALE" if is_stale(l) else "     ",
                 l["id"], "" if l["dismissible"] else "  [not dismissible]"))
        note = opportunity_note(l)
        if note:
            print("           " + note)
        if not is_decision(l):
            continue
        if l["text"]:
            print("           " + l["text"])
        for c in l["choices"]:
            print("           [%s] %s%s"
                  % (c["index"], c["text"],
                     (" -- DISABLED: %s" % c["disabledReason"]) if c["disabled"] else ""))


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--no-hold"]
    hold = "--no-hold" not in sys.argv[1:]
    cmd = a[0] if a else "list"
    if cmd in ("--windows", "windows"):
        # The raw window list, unranked and unfiltered. Added 2026-09-02: when
        # dialog_window() names something surprising, this is what it saw.
        rim.init()
        st = rim.game("rimworld/get_ui_state", {}, strict=False)
        ws = (st or {}).get("windows") or []
        print("focusedWindowType: %r   nonImmediateDialogWindowOpen: %r"
              % ((st or {}).get("focusedWindowType"),
                 (st or {}).get("nonImmediateDialogWindowOpen")))
        if not ws:
            print("windows: none reported (checked, not assumed)")
        ranked = [id(w) for w in windows_ranked(st or {})]
        for i, w in enumerate(ws):
            t = w.get("type") or "?"
            marks = []
            if t.startswith(IGNORE_WINDOWS):
                marks.append("IGNORED (never the modal)")
            if id(w) in ranked:
                marks.append("candidate #%d" % ranked.index(id(w)))
            print("  [%d] %-52s layer=%-8s absorb=%-5s forcePause=%-5s %s"
                  % (i, t, w.get("layer"), w.get("absorbInputAroundWindow"),
                     w.get("forcePause"), "  ".join(marks)))
        print("-- dialog_window() -> %r" % dialog_window())
        sys.exit(0)
    if cmd == "sweep":
        # The sweep by hand. Added 2026-09-02 with auto_dismiss: `watch.py` and
        # `run.py` call it every step, and this is how you watch it decide
        # without playing a step. `python letters.py sweep 0` sweeps everything
        # eligible immediately -- useful once, against a stack that has piled
        # up, and not what the tools do.
        rim.init()
        stack = pending()
        after = float(a[1]) if len(a) > 1 else AUTO_DISMISS_S
        gone = auto_dismiss(stack, after=after)
        for l in stack:
            why = keep_reason(l)
            if why:
                print("   KEPT %-40s %s" % ((l["label"] or "")[:40], why))
        print("-- %d of %d dismissed at %.0fs; the rest are listed above with the"
              " reason each one is still standing." % (len(gone), len(stack), after))
        sys.exit(0)
    if cmd in ("open", "dismiss") and len(a) < 2:
        # Checked before init() so a usage slip doesn't need a live bridge.
        print("python letters.py open <letter id>  (ids from `python letters.py`)")
        sys.exit(2)
    rim.init()
    if cmd == "dismiss":
        result = dismiss(a[1])
        print(json.dumps(result, indent=1))
        sys.exit(0 if isinstance(result, dict) and result.get("dismissed") is True else 1)
    elif cmd == "open":
        # Look the row up first: the letter's own `choices[]` are what say
        # whether there is anything to answer, and without it a NO DIALOG
        # OPENED cannot tell an opportunity quest from a failure.
        row = next((l for l in pending() if str(l.get("id")) == str(a[1])), None)
        if row is not None:
            note = opportunity_note(row)
            if note:
                print("   ~~ " + note)
        got = open_letter(a[1], hold=hold, letter=row)
        if got:
            print("dialog:", got["window"])
            for o in got["options"]:
                why = getattr(o, "disabledReason", None)
                print("   %s %s%s" % ("*" if o[1] else " ", o[0],
                                      "   [DISABLED: %s]" % why if why else ""))
            print("-- decide with: python letters.py decide \"<option text>\"")
    elif cmd == "decide":
        ok = decide(a[1] if len(a) > 1 else None, hold=hold)
        # Not "still open": a False also covers a refusal and an unverified
        # read, and both are printed above with what they actually mean.
        print("dialog closed" if ok else "NOT closed -- see the line above")
    else:
        rows = pending()
        if not rows:
            print("no letters on the stack")
            sys.exit(0)
        show(rows)
        n = sum(1 for l in rows if is_decision(l))
        info = sum(1 for l in rows if navigate_only(l))
        stale = sum(1 for l in rows if is_stale(l))
        print("-- %d STALE (older than %.0f game hours: history, not news);"
              " %d INFO (only jump/close buttons -- an opportunity quest has"
              " nothing to answer and the sweep may take it)."
              % (stale, STALE_HOURS, info))
        print("-- %d letters, %d needing a decision (ANNOUNCE rows are label"
              " only; a Close button is not a choice -- see ACKNOWLEDGE)."
              " Since 2026-09-02 watch.py and run.py both sweep through"
              " auto_dismiss: an announcement goes ten REAL seconds after it"
              " first appears, never on the sweep that first sees it; CHOICE and"
              " OPEN stay until somebody answers them." % (len(rows), n))
