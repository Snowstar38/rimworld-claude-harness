# NEWGAME — driving RimWorld's new-colony flow through the bridge

## Implemented lightweight helper (2026-09-08)

`python newgame.py --help` is the small, state-aware workflow built from this
survey. It hardcodes only the three blind 4096x2160 clicks (main-menu New
colony, random landing site, landing-site Next), uses text clicks for ordinary
pages, waits for world generation, and refuses when the expected page is not
open. `python startpawns.py` reads the pawn page by candidate instead of trying
to decipher the interleaved layout. The final Start click is deliberately left
to the player.

`see.py` now works in the Entry scene, and `rim.py call ... --out FILE`
preserves large raw replies. Radio/icon selection-state source support is
implemented in the upstream checkout but not installed: that checkout currently
needs a newer .NET SDK to build. Until it is deployed, `newgame.py storyteller`
prints the remaining `[?]` state and names `see.py` as the optional strict check.

**What this is.** A findings report from one full walk of the new-colony creation
flow (main menu → scenario → storyteller → world → landing site → ideology →
starting pawns), done on 2026-09-08 by errata at M's request. The purpose
is to say **which steps are already drivable, which are blind, and what is worth
building**, so tools get built for the hard parts only.

**Status: the colony was never started.** The walk stopped in the ideoligion
editor. Nothing was saved. This is a survey, not a playthrough.

**Read this as a lead, not testimony.** Every claim below is marked as either
VERIFIED (I saw it happen this session) or INFERRED. Re-check anything you are
about to build on — the PLAYBOOK's own rule.

---

## 1. Environment this was measured in

| Thing | Value |
|---|---|
| RimWorld | 1.6.4871 rev591, compiled Jun 29 2026, 64-bit |
| Display | fullscreen **4096x2160** — a bridge screenshot pixel IS a screen pixel |
| Profile | `C:\Home\rimworld\bridge\profile` (`-savedatafolder=`) |
| Companion | HomeBridge, `home/ping ok, sdk 2.1.1.0` |
| Screenshots land in | `C:\Home\rimworld\bridge\profile\Screenshots\<name>.png` |

Active mods (from `profile\Config\ModsConfig.xml`) — all QoL, none known to alter
these pages, but **`ferny.noodysseycategory` and `wtfomgjohnny.perishable` are
unverified in that respect**:

```
brrainz.harmony, ludeon.rimworld, royalty, ideology, biotech, anomaly, odyssey,
brrainz.rimbridgeserver, unlimitedhugs.hugslib, unlimitedhugs.allowtool,
owlchemist.doorclearance, jaxe.bubbles, ferny.noodysseycategory,
wtfomgjohnny.perishable, mehni.pickupandhaul, tixiv.whoshotmylegoff
```

If you re-run this survey with a different mod list, **say so** — the Ideology
and Odyssey expansions in particular add whole pages (`Page_ChooseIdeoPreset`,
the Gravship scenario) that would not exist otherwise.

### Getting the game up for this work

`python setup.py` with **no** load flag starts the stack and leaves RimWorld at
the main menu. That is the correct entry point for this flow; `--newest` /
`--load` skip it entirely.

**A RimWorld that restarted itself (e.g. after a mod change) cannot be
re-attached.** VERIFIED: `rim.py g games_connect '{"gameId":"rimworld"}'`
returns

```
Failed to resolve live GABP endpoint for 'rimworld': no runtime claim exists
for 'rimworld'; nothing is attachable — start the game, or check games_status
```

and `games_status` reports `stopped` even while the process is alive. The
runtime claim is created **at launch by GABS** and cannot be established after
the fact. The only resync is: close the process, then `python setup.py`. Mods
survive that, because they live in the profile's `ModsConfig.xml`.
(Obey the PLAYBOOK's exit-2 rule first — an unconnected RimWorld may be
M's own game. Ask before closing anything.)

---

## 2. The page sequence (VERIFIED)

Window types, in order, as reported by `rimworld/get_screen_targets`:

```
(main menu — NO window at all)
  -> RimWorld.Page_SelectScenario
  -> RimWorld.Page_SelectStoryteller
  -> RimWorld.Page_CreateWorldParams
  -> RimWorld.Page_SelectStartingSite      [+ Verse.Dialog_MessageBox warnings]
  -> RimWorld.Page_ChooseIdeoPreset
       |- "Ideology system inactive" -> straight to pawns
       `- "Create custom / (fixed)"  -> RimWorld.Page_ConfigureIdeo
                                          + RimWorld.Dialog_ChooseMemes
  -> RimWorld.Page_ConfigureStartingPawns  -> Start
```

Navigation between pages is `Next` / `Back`, and **`ui.py click "Next"` works on
every page that has a real window** (all of them except the main menu and the
landing-site bottom bar).

---

## 3. Findings per step

### 3.1 Main menu — HARD (completely blind)

VERIFIED. `rimworld/get_screen_targets` at the main menu returns:

```json
"uiState": { "programState": "Entry", "inEntryScene": true,
             "windowCount": 0, "topWindowType": null },
"windows": []
```

RimWorld draws the main menu in the entry scene as immediate-mode GUI
(`MainMenuDrawer`); it is **not a Window**, so there is nothing to enumerate.
`ui.py --surfaces` dies with:

```
rim.BridgeError: rimworld/get_ui_layout: Timed out waiting 2000ms for a UI
surface to draw. Open a dialog, main tab, or selected gizmo grid and retry.
```

**What does work:** `rimworld/take_screenshot` succeeds in the entry scene
(VERIFIED — it does not need a map), and `click.py <x> <y>` drives the real
mouse. So: screenshot, read it, click a pixel.

**Verified coordinate:** `New colony` = **`python click.py 3103 745`**.

**Cost to fix: near zero.** The menu is static, so one hardcoded click ends this
problem permanently. This is the cheapest win in the report.

### 3.2 Scenario select — EASY

VERIFIED. `Page_SelectScenario` reads cleanly and every option clicks by text:

```
*  Lost Tribe / The Rich Explorer / Naked Brutality / The Mechanitor
*  The Sanguophage / The Anomaly / The Gravship
*  Back    * Scenario editor    * Next
```

**One wrinkle:** the *currently selected* scenario renders as plain text rather
than a button (Crashlanded appeared as `.` not `*`). So "which is selected" is
only inferable from which row is **not** clickable. That is fragile — do not
build a check on it without confirming it holds for other scenarios (INFERRED
from a single observation).

`Scenario editor` was **not tested**.

### 3.3 Storyteller + difficulty — HARDEST for reading state

This step caused the only real mistake of the session and is the strongest
argument for tooling.

**(a) Radio state is not in the data.** VERIFIED. Every radio row reads `[?]`.
`ui.py` says so itself:

```
RADIO rows read [?]: the layout carries no chosen flag for a radio button,
so WHICH option is selected is not in the text at all. `python see.py` for
the highlight.
```

**(b) The documented fallback crashes here.** VERIFIED. `see.py` fails in the
entry scene because it sets camera zoom first and there is no camera:

```
see.py FAILED: set zoom failed: System.NullReferenceException ...
at RimBridgeServer.ViewCapabilityModule.SetCameraZoom (System.Single rootSize)
```

So the tool the layout points you to is unusable at exactly the place it is
needed. **Fixing `see.py` to no-op the zoom outside a game is a small, high-value
patch.**

**(c) Storytellers are nameless icon buttons.** VERIFIED. Cassandra / Phoebe /
Randy appear only as:

```
[0] icon_button  centre 388,178
[1] icon_button  centre 388,308
[2] icon_button  centre 388,438
```

`ui.py click --index 1` works (VERIFIED — selected Phoebe). **Mitigation
found:** the *selected* storyteller's name appears as a text row afterwards
(`. Phoebe Chillax`), so identity is verifiable by reading back after a blind
click. Index→name is discoverable, not predictable.

**(d) Row-merging hides whole options.** VERIFIED. Rows are grouped by
y-position, so a radio label level with a line of description text is swallowed
by it. On screen there are nine options; the layout showed seven. Missing:
**Community builder** (merged into the Cassandra blurb) and **Commitment mode**
(merged into the AI Storyteller blurb). The merge is *positional and unstable* —
after switching to Phoebe it re-merged differently (`Phoebe Chillax Community
builder`).

**(e) Consequence, and the reason this matters.** VERIFIED: three consecutive
`ui.py click` calls each reported `success`, and the resulting state was wrong in
two places — difficulty was **Community builder** (not the intended Strive to
survive, because the same radio group overwrote it) and **Commitment mode**
(permadeath) was ON. Only a screenshot revealed it. An unattended agent would
have started a permadeath colony without ever knowing.

Text-clicking these radio labels **does** work (Community builder went green),
it is the *reading back* that is impossible.

### 3.4 World generation params — MEDIUM, ambiguous

VERIFIED. `Page_CreateWorldParams` is a two-column page (settings left, factions
right) and the reader interleaves them by height:

```
.  Overall temperature  Savage impid tribe
.  Low  High  Normal  (text not unique)
.  Sparse  Crowded  Normal  (text not unique)
```

- `Low High Normal` appears **three times**, marked *text not unique*, for
  rainfall / temperature / population. There is no way to tell which is which, or
  which value is active. **Setting these by text is not currently safe.**
- Globe coverage is the exception — it exposes its value (`50%`) as a clickable.
- `Generate` works by text click. World generation is **fast** (VERIFIED: idle
  within seconds) and `rimbridge/wait_for_long_event_idle` is the correct wait.

`Advanced settings / Edit...`, `Reset all`, `Reset factions`, and per-faction
toggles were **not tested**.

### 3.5 Landing site — HARD to click, EASY to judge

**The page is blind.** VERIFIED — `Page_SelectStartingSite` reads:

```
1 elements -> 0 rows, 0 actionable
no readable text on this surface: checked, not assumed
```

`click.py`'s own docstring already documented why: the page has a zero-size
window rect and draws its buttons in `ExtraOnGUI`.

**`world.py` does not help here.** VERIFIED — it returns every field as `?`,
because it reads the *current colony's* tile and there is no colony yet. At the
one moment you are choosing a tile, the world instrument is blind.

**Verified bottom-bar coordinates** (4096x2160):

| Button | Command | Status |
|---|---|---|
| Select random site | `python click.py 1845 2075` | VERIFIED |
| Next | `python click.py 2644 2075` | VERIFIED |
| Back | `python click.py 1444 2075` | computed, UNVERIFIED |
| Factions | `python click.py 2245 2075` | computed, UNVERIFIED |

**The big mitigation: a selected tile is fully readable.** VERIFIED. Once any
tile is selected, a `RimWorld.Planet.WorldInspectPane` plus a `Verse.
ImmediateWindow` appear and give the entire tile card as text:

```
Tropical rainforest
6.53N 27.03E / Flat / Movement difficulty: 2 / Average temperature: 31.4C
Stone types    Granite, sandstone and limestone
Growing period 30/60 days (6th of Aprimay - 6th of Septober)
Rainfall 2058mm | Elevation 552m | Forageability 100% (berries)
Average disease frequency 1.2 per year | Pollution 0%
```

So the workable loop is **one blind click, then a full text read**. Evaluating
and rejecting tiles needs no vision at all.

**Aimed clicking works.** VERIFIED with a falsifiable test: I aimed at the
on-globe label "Hisler Rainforest" (`click.py 2437 512`) and the pane came back
`Tropical rainforest`. Accuracy is region-level, not guaranteed-single-hex, but
**a miss is always detectable** because the pane names what you actually hit.
This is what makes "chat says go there" viable on the globe.

**Warning modals are easy.** VERIFIED — `Verse.Dialog_MessageBox` is fully
readable and text-clickable (`Go back` / `Confirm`). Two different ones fired:
encroaching within 4 tiles of a faction base, and acidic smog every ~215 days.
Expect more; handle them generically.

### 3.6 Ideology — MIXED, and the editor is the best page in the flow

**Preset page** (`Page_ChooseIdeoPreset`): EASY, all text-clickable —
`Ideology system inactive`, `Create custom / (fluid)`, `Create custom / (fixed)`,
`Load saved...`, plus generated presets. Selecting a row does **not** advance;
`Next` does (VERIFIED).

**Icon tiles are unreliable by text.** The structure picker and meme picker are
icon grids. VERIFIED: `ui.py click "Collectivist"` worked (validation cleared),
while `ui.py click "Ideological"` appeared not to (validation persisted) and a
raw pixel click at `click.py 1270 573` did work. INFERRED cause: whether the text
draw sits inside the button rect is positional luck. **Treat icon-tile text
clicks as unreliable and always verify.**

**Best verification trick found — red validation text is readable.** VERIFIED.
The dialog's own error text appears in the layout (`Choose structure.`,
`Choose at least 1 meme.`), so its **disappearance is a free success signal** with
no screenshot. This is the single most useful technique in this report and
generalises to any page with validation.

**`Dialog_ChooseMemes` is used for BOTH stages.** VERIFIED, and it cost me a
wrong conclusion mid-session: the structure stage and the meme stage are the same
window type, so "did Done advance or fail?" is **invisible from the window type
alone**. Distinguish by content (`Choose structure` vs `Choose memes`), never by
type. Verified `Done` on that dialog = `click.py 3035 1888`.

**The precept editor is excellent.** VERIFIED. `Page_ConfigureIdeo` reads every
precept as a name+value pair and a full edit round-trip works with **no
screenshots**:

```
python ui.py click "Slavery Abhorrent"     -> opened Verse.FloatMenu
python ui.py click "Acceptable"            -> closed Verse.FloatMenu
python ui.py                               -> "Slavery Acceptable"   (confirmed)
```

Readable rows include: Memes, Narrative, Deities, structure, and precepts such as
Drug use, Child labor, Execution, Slavery, Cannibalism, Organ use, Work drive,
Corpses, Blindness, Fungus, Research, plus `Overall impact: Low`, `Randomize
all`, `Add precept...`, `Save` / `Load`.

**Conclusion: building a static (fixed) ideoligion is already drivable** apart
from the icon-tile clicks. This step needs the *least* new tooling of the hard
ones.

Not tested: `Add precept...`, `Add deity`, `Save`/`Load`, `Xenotype editor`,
`Anomaly settings...`, the Appearance rows.

### 3.7 Starting pawns — HARDEST overall, and it has NO instrument

VERIFIED. The obvious tool refuses outright:

```
pawns.py FAILED: BridgeError: home/list_pawns refused:
home/list_pawns requires an active map.
```

Every `home/*` pawn tool is map-scoped, so at character creation there is
**nothing but the raw layout** — and the raw layout is at its worst here, because
the page is multi-column and the y-grouping scrambles it:

```
.  Sanchez        .  Frightened child  Age 19 (59)
.  Childhood  Frightened child  Shooting  -
.  Cooking  4  4        .  Dems  Animals  3
.  Discharged soldier  Crafting  3
.  Team skills
.  Shooting Construction Cooking Medical   10  3  4  7
.  Melee Mining Plants Intellectual        4   3  3  10
```

Names, backstories, traits and skill numbers interleave across pawns; skill
labels are separated from their values. **The only reliably parseable thing is
the team-totals row** (labels in order, then values in order).

This is where a purpose-built tool pays for itself most.

---

## 4. Cross-cutting hazards (read this section even if you skip the rest)

1. **`ui.py click` reporting `success` does not mean the click did anything.**
   VERIFIED in three distinct ways this session:
   - it clicked an element on a surface sitting **behind a modal** and reported
     success while nothing happened;
   - it clicked a row marked **`~offsurface`** (scrolled out of view) and
     reported success;
   - it clicked radio labels that selected the wrong thing, reporting success
     each time.

   `success` means *a click was dispatched*, not *the intended state changed*.
   The read-back line (`opened X / closed Y`) is trustworthy when a window
   actually changes; `nothing opened or closed` carries no information.

2. **There is no scroll route through the wrappers.** VERIFIED: `ui.py` hides
   `targetId` from its output ("dropped from every row"), and
   `rimworld/scroll_ui_target` requires exactly that id. Dropping to the raw
   layout fails too — see (3). `winctl.py` has `move_screen`, `button`,
   `glide_screen` but **no wheel function**. So an `~offsurface` row is currently
   unreachable.

3. **Raw layout output is truncated at 8000 characters.** VERIFIED —
   `rim.py call rimworld/get_ui_layout '{}' > file` produced invalid JSON:
   `Unterminated string starting at: line 391 column 16 (char 7999)`. You cannot
   harvest targetIds for large surfaces this way.

4. **Selection/checked state is absent across the whole entry scene.** Radios,
   icon tiles and highlighted rows all read `[?]` or as plain text. This one gap
   is upstream of nearly every difficulty above.

5. **`ui.py --help` does not print help** — it performs a UI capture instead.
   Minor, but it wastes a call and a bridge round-trip.

---

## 5. What already works — the toolbox to build on

| Capability | Command | Status |
|---|---|---|
| Screenshot anywhere, incl. entry scene | `rim.py call rimworld/take_screenshot '{"fileName":"x"}'` | VERIFIED |
| Window/state probe, works with 0 windows | `rim.py call rimworld/get_screen_targets '{}'` | VERIFIED |
| Structured read of any real window | `python ui.py` | VERIFIED |
| Click by text | `python ui.py click "<text>"` | VERIFIED (unreliable on icon tiles) |
| Click a text-free element | `python ui.py click --index N` | VERIFIED on storyteller portraits |
| Real mouse, screen pixels | `python click.py <x> <y>` | VERIFIED |
| Wait out world generation | `rim.py call rimbridge/wait_for_long_event_idle '{"timeoutMs":240000}'` | VERIFIED |
| Read a chosen world tile | `python ui.py` (WorldInspectPane) | VERIFIED |

**Verification signals that need no screenshot:** window open/close read-back;
red validation text appearing or clearing; the selected storyteller's name; the
tile card; precept name+value rows.

**Not available without vision:** which radio is chosen; which icon tile is
highlighted; which scenario is selected.

---

## 6. Proposed work, ranked

Ranked by (pain removed) / (effort). Items 1 and 2 are the ones M's
"only the really hard parts" test clearly passes.

**T1 — Selection-state in the layout capture.** *Highest value.* Make the
companion report chosen/highlighted state for radio buttons and icon tiles, so
`ui.py` can print `[x]` instead of `[?]`. This is a companion-side change
(`C:\Home\rimworld\companion`, see its `INSTALL.md`); the capture already walks
these controls, it simply does not emit the flag. **Acceptance:** on
`Page_SelectStoryteller`, `python ui.py` names the selected difficulty and the
selected storyteller with no screenshot. Everything painful in §3.3, §3.4 and
§3.6 collapses if this lands.

**T2 — `startpawns.py`.** *Highest value, no alternative exists.* A structured
reader (and ideally writer) for `Page_ConfigureStartingPawns`: per-pawn name,
backstories, traits, "incapable of", skills with passions, plus the team totals
and the `Randomize` / `Start` controls. Must work with **no active map**, so it
cannot use `home/list_pawns`; it will need either a new companion capability or
careful column-aware parsing of the raw layout. **Acceptance:** prints each
starting pawn separately, with skills correctly attributed, on Crashlanded.

**T3 — Make `ui.py click` honest.** Refuse (or loudly warn) when the target row
is `~offsurface` or belongs to a surface that is not the topmost modal, and
report "no observable change" rather than `success` when nothing opened, closed
or changed. **Acceptance:** the three false-positive cases in §4.1 each produce a
distinguishable non-success result.

**T4 — Fix `see.py` outside a game.** Skip the camera-zoom step when
`programState` is `Entry` (or when the zoom call throws) so the visual fallback
works on the menu pages. Small patch, removes a documented dead end. Lower
priority if T1 lands, since T1 removes most of the need.

**T5 — `newgame.py`, an end-to-end driver.** Once T1–T3 exist: one command that
walks main menu → Start with named arguments (`--scenario Crashlanded
--storyteller Cassandra --difficulty "Strive to survive" --reload-anytime
--ideology none|fixed`), verifying after every step and refusing to advance on an
unverified selection. Hardcode the main-menu click (§3.1). **Acceptance:** lands
in a playable colony from a cold main menu, and fails loudly rather than
silently mis-setting anything.

**T6 — Scrolling.** Expose `scroll_view` targetIds through `ui.py` and add a
`ui.py scroll` verb, and/or add a wheel function to `winctl.py`. Needed for long
precept lists and any long scenario/meme list.

**T7 — `rim.py call --out FILE`** to bypass the 8000-character truncation, so
raw layouts are usable for large surfaces. Trivial, unblocks T6 debugging.

---

## 7. Open questions / not tested

- Whether text-clicking the structure tile genuinely fails, or whether my
  observation was confounded — **one observation only**, and the contrary case
  (Collectivist) succeeded. Re-test before designing around it.
- Everything after `Start`: the colony was never created, so the landing itself
  and any post-landing dialog are unsurveyed.
- `Scenario editor`, `Xenotype editor`, `Anomaly settings...`, `Factions`,
  `Advanced settings / Edit...`, `Add precept...`, `Add deity`, ideoligion
  `Save`/`Load`.
- Whether the mod list changes any of these pages.
- Whether the verified pixel coordinates hold at other resolutions — **they
  almost certainly do not**. Everything in §3.1 and §3.5 is 4096x2160-specific.
  A resolution-independent approach (or a coordinate calibration step) is worth
  considering for T5.

## 8. Loose end left in the game

The walk ended inside `Page_ConfigureIdeo` with a custom fixed ideoligion
(Ideological/Ideologist, Collectivist meme). While testing the precept editor I
set **Slavery to Acceptable** and did not restore it: the row had scrolled
`~offsurface` and, per §4.2, there is no scroll route. On a throwaway test world
that was not worth more time — but if that world is ever reused, fix it first.
