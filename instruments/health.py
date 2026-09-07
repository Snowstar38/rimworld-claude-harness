"""The formatter behind `pawns.py --health`: health records into lines.

No CLI and no doc row of its own -- `python pawns.py --health [--needs] [--all]
[--hidden] [--name X]` is the command. This file turns one `home/list_pawns`
row into the strings that view prints, and owns the sort key that decides which
colonist to look at first, so nothing else has to know what "extreme" or "worst"
means.

  import health
  health.summary(row)       -> the one-line condition string
  health.needs_summary(row) -> food / rest / joy / mood, hunger, break risk
  health.conditions(row)    -> [condition label, ...], worst first
  health.label(c)           -> one condition, the way the Health tab writes it
  health.rows()             -> the pawn records, with .health / .needs (one call)
  health.report()           -> {name: [condition label, ...]}
"""
import rim

TOOL = "home/list_pawns"


def _call(**kw):
    r = rim.game(TOOL, kw)
    if not isinstance(r, dict) or not r.get("success") or "pawns" not in r:
        # A companion that is not loaded must be loud. "everyone is healthy" is
        # the most dangerous empty answer this stack can print.
        raise rim.BridgeError(
            "%s did not answer (%s). Is the HomeBridge companion DLL installed "
            "and was RimWorld restarted since? See rimworld\\companion\\"
            "INSTALL.md" % (TOOL, (isinstance(r, dict) and r.get("error")) or r))
    return r


def rows(everyone=False, needs=True, hidden=False):
    """Pawn records with `health` (and `needs`) blocks attached.

    Colonists only by default. `everyone=True` adds every other living pawn on
    the map, ours and wild -- the tame warg's health is a real question and
    nothing else answers it. `hidden=True` turns off the game's own
    `Hediff.Visible` filter -- the one the Health tab draws by.
    """
    r = _call(health=True, needs=bool(needs), visibleHediffsOnly=not hidden)
    out = []
    for p in r["pawns"]:
        if p.get("dead"):
            continue
        if not everyone and not p.get("isColonist"):
            continue
        out.append(p)
    return out


def label(c):
    """One condition, the way the Health tab writes it.

    `label` is RimWorld's own -- "Hypothermia (extreme)", "Cut (moderate)" -- so
    the severity word is already in the string. The body part is appended
    because half the labels are meaningless without it: the legacy tool printed
    "Lost to frostbite" and could not say which toe, and two of them on one
    colonist read as a duplicate rather than as two lost toes.
    """
    text = c.get("label") or ""
    part = c.get("part")
    if part and part.lower() not in text.lower():
        text += " [%s]" % part
    return text


def conditions(p):
    """Every condition label on one pawn record, worst first.

    The DLL has already sorted them life-threatening, then bleeding, then
    severity.

    **NO WATCH LIST.** Every hediff handed in is returned. A curated list of
    condition names once silently dropped food poisoning while a colonist had
    it, and a reader that drops the category the answer is in is worse than no
    reader. The only filter is the game's own `Hediff.Visible` -- what the
    Health tab itself draws -- applied in the DLL and counted in
    `hediffsHiddenByVisibleFilter`. If you find yourself adding a filter here,
    don't.
    """
    h = p.get("health") or {}
    return [label(c) for c in (h.get("hediffs") or []) if c.get("label")]


def report(everyone=False):
    """{name: [condition label, ...]} -- the old shape, one call instead of 5N."""
    return dict((p.get("name"), conditions(p)) for p in rows(everyone=everyone, needs=False))


# ------------------------------------------------------------------ formatting

def _pct(v):
    return "-" if v is None else "%d%%" % round(v * 100)


def summary(p):
    """The one-line severity string. Shared with `pawns.py --health`.

    Conditions first because that is the question; the numbers the labels do not
    carry follow. Empty string when there is genuinely nothing to say.
    """
    h = p.get("health") or {}
    bits = []
    conds = conditions(p)
    if conds:
        bits.append(" | ".join(conds))
    if p.get("downed"):
        bits.insert(0, "DOWNED")
    hours = h.get("hoursUntilDeathFromBloodLoss")
    if hours is not None:
        bits.append("BLEEDING OUT in %.1fh" % hours)
    elif h.get("bleeding"):
        bits.append("bleeding %.2f/d" % (h.get("bleedRatePerDay") or 0))
    if h.get("needsTend"):
        bits.append("NEEDS TENDING")
    pain = h.get("painTotal")
    if pain:
        bits.append("pain %s" % _pct(pain))
    imp = h.get("capacitiesImpaired") or {}
    if imp:
        bits.append(" ".join("%s %s" % (k, _pct(v)) for k, v in sorted(imp.items())))
    # Off by default for everyone including new joiners, and fatal in a colony
    # where one person is the only doctor. Only worth saying about a colonist
    # who has something to tend.
    if p.get("isColonist") and h.get("selfTend") is False and (conds or h.get("needsTend")):
        bits.append("self-tend OFF")
    return "  ".join(bits)


def needs_summary(p):
    n = p.get("needs") or {}
    if not n:
        return ""
    bits = []
    for key, label in (("food", "food"), ("rest", "rest"), ("joy", "joy"), ("mood", "mood")):
        if n.get(key) is not None:
            bits.append("%s %s" % (label, _pct(n[key])))
    cat = n.get("hungerCategory")
    if cat and cat not in ("Fed",):
        bits.append(cat.upper())
    risk = n.get("breakRisk")
    if risk and risk != "none":
        # mood.CurLevelPercentage against the pawn's OWN thresholds. This is the
        # number that lets you see a break coming; `mentalState` is only non-null
        # once it already happened.
        bits.append("BREAK RISK %s (minor at %s)" % (risk.upper(), _pct(n.get("breakThresholdMinor"))))
    return "  ".join(bits)


def _worst(p):
    """Sort key: the colonist to look at first.

    **`severity` is deliberately not the primary key.** It is per-HediffDef and
    not comparable across defs -- on this colony Malnutrition reads 0.002, a
    missing toe reads 0.5 and a twenty-year-old gunshot scar reads 3.0. Sorting
    on it puts the oldest scar in the colony at the top of the list.

    So: the flags that mean "act now" first, then RimWorld's OWN cross-pawn
    number (`summaryHealth.SummaryHealthPercent`, the bar at the top of the
    Health tab), then pain, then how many things are wrong.
    """
    h = p.get("health") or {}
    hediffs = h.get("hediffs") or []
    return (
        0 if p.get("downed") else 1,
        0 if h.get("anyLifeThreatening") else 1,
        0 if h.get("hoursUntilDeathFromBloodLoss") is not None else 1,
        0 if h.get("needsTend") else 1,
        h.get("summaryPct") if h.get("summaryPct") is not None else 1.0,
        -(h.get("painTotal") or 0),
        -len(hediffs),
        (p.get("name") or "").lower(),
    )


if __name__ == "__main__":
    # Not a command. Anything that used to be `python health.py` is a flag on
    # `pawns.py` now; the two were one bridge call all along.
    print("health.py is the formatter behind pawns.py -- it has no CLI.")
    print("Run: python pawns.py --health [--needs] [--all] [--hidden] "
          "[--name X] [--json]")
