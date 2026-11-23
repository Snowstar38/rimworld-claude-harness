# Efficiency audit — where the toolkit still reads the game the slow way

**2026-09-02, evening. `Lampblack — day 39`, game running and paused throughout.**
**Audited by Fable 5.1.**

M's ask, verbatim:

> *"a lot of the original bridge does stuff in a more Game UI-central way, and
> alternatively through relatively inefficient ways, so i wanna make sure we are
> doing it in the maximally efficient way (by having direct access to all that
> info through the custom bridge)."*

Every number here was measured live on day 39 (paused, read-only, through
`rim.py` with `RIM_TIMING` on) or read from the `Assembly-CSharp.dll` decompile.
Nothing is estimated unless marked **[E]**.

## Shipped since this was written

- `BridgeCommon.cs` + `unknownArguments[]` on every tool — built, installed, live-verified on all twelve tools 2026-09-02.
- `bio` and `thoughts` blocks on `home/list_pawns`, and apparel `bodyPartGroups[]` — shipped.
- `alerts.py` reading `explanation`/`targets[]`, and `letters.options()` reading `choices[]` from the payload instead of scraping — shipped.
- `health.py` folded into `pawns.py` — shipped.
- `home/list_rooms` + the seventh ASCII map layer (WANTED 15) — **in progress 2026-09-02**.
- `home/pawn_config`, plus `work`/`schedule`/`settings`/`relations` blocks on `list_pawns` (WANTED 1, 2, 11) — **in progress 2026-09-02**.
- `home/research` and `research.py` (C4, build order 5; WANTED 13) — shipped 2026-09-03. The current project, the benches, the researchers and every startable project in one call, plus the write; no tab is opened.
- `inspectString` on `home/list_buildings` rows behind `inspect: true` (C5, build order 7; WANTED 14) — shipped 2026-09-03. `Thing.GetInspectString()` needs no selection, so the stated problem dissolved.
- The `animals` block on `home/list_pawns`, with `training`/`slaughter`/`releaseToWild` on `home/pawn_config` (D8, build order 8; WANTED 7) — shipped 2026-09-03. `wildness` turned out to be `StatDefOf.Wildness` in 1.6, not a `RaceProperties` field.
- `home/bills` and `bills.py`, plus `billIngredients: true` on `home/list_buildings` (D7, build order 6; WANTED 4) — shipped 2026-09-03. One tool, not the `list_bills`/`set_bill` pair proposed below, and it answers the question the audit could not: per ingredient, needed against what is on the map, so a bill that cannot run says why.
- `home/building_config` and `buildings.py gizmos / set` (D10, build order 9; WANTED 12) — shipped 2026-09-03. The four named direct calls won: forbidden, power, medical bed, bed owner, plus `forPrisoners`, with the gizmo bar readable but never fired. `Thing.GetGizmos()` calls `Faction.OfPlayer`, so the read refuses rather than pausing when there is no player faction.
- `home/status` and `status.py` (WANTED 10) — shipped 2026-09-03. Clock, letters, messages, alerts, colonists, threats and the UI state in one ~7 KB call, built the `list_pawns` way: opt-in blocks, every block always present.
- `Watch.cs` and the `watch`/`watchSeconds` parameters on every write tool (WANTED 0) — shipped 2026-09-03. The menu a player would use opens first, the write lands inside it, the menu closes itself.
- Prebuilt stockpile filters: `home/zone_cells` `op: "filter"` with six presets, `create` taking the same keys, and `home/list_zones {filter: true}` (WANTED 5) — shipped 2026-09-03.
- Category filters and `nameFilter` on `home/list_pawns` (WANTED 2) — shipped 2026-09-03. Nine bools ANDed server-side, each with a matching bool on the row.
- `home/get_cells_plus {summary: true}` freed from the 1024-cell cap, which now applies only to the `cells[]` modes (WANTED 17) — shipped 2026-09-03.

The stock tool list has since been checked: the stock bridge has **no** work,
schedule, room, research or bill tools. The "no path exists" claims below stand.

---

## 1. Summary

**41 distinct reads** were classified.

| Category | Count | Verdict |
|---|---|---|
| **A. Direct** — a `home/` companion read, one call, nothing on screen | 13 | Confirmed. §4. |
| **B. Structured but stock** — a `rimworld/` tool returning real fields cheaply | 11 | Leave alone. §4. |
| **C. UI-path** — selects, opens a tab, or scrapes `get_ui_layout` | 5 live + 3 retired or fallback-only | Targets. §2. |
| **D. No read path at all** — guessed, asked a human, or written into a chronicle by hand and re-read as fact | 10 | Higher priority than C. §2. |

Two thirds of the toolkit's reads are already direct or already cheap. What is
left is mostly **missing**, not slow.

---

## 2. Still open

Columns: **cost today**, **direct read**, **API** (every name below was printed
by ILSpy in this session), **WANTED #**.

### D — no read path at all

| # | What | Cost today | Direct read | API | WANTED |
|---|---|---|---|---|---|
| **D7** ✅ | **SHIPPED as `home/bills`.** **Bills on a workbench.** What is queued, and whether it will run. | No path. | `home/list_bills` (read) then `home/set_bill` (write). | `BillStack.Bills` (`List<Bill>`), `.Count`, `.AnyShouldDoNow`, `.FirstShouldDoNow`, `MaxCount = 15`; `Bill.recipe/.suspended/.ingredientFilter/.ingredientSearchRadius/.allowedSkillRange/.PawnRestriction/.LabelCap`; `Bill_Production.repeatMode/.repeatCount/.targetCount/.paused/.pauseWhenSatisfied/.unpauseWhenYouHave/.hpRange/.qualityRange/.RepeatInfoText/.ShouldDoNow()` | **4** |
| **D10** ✅ | **SHIPPED as `home/building_config`.** **Gizmo inventory for a thing**, and the dialogs a fired gizmo opens. | No read path; firing one today loses any dialog it opens. | `home/list_gizmos` + direct calls for the four named cases (bed owner, medical bed, forbidden, power). | `Thing.GetGizmos()` → `IEnumerable<Gizmo>` (public virtual); `Command`/`Command_Toggle` | **12** |

D9 (alert detail) collapsed into D6, which shipped.

### C — UI-path

| # | What | Cost today | Direct read | API | WANTED |
|---|---|---|---|---|---|
| **C3** | **`move.goto`.** `move.py:50-76`. | **8 bridge calls minimum, five of them mutations**: `get_ui_state`, `get_cell_info`, `set_camera_zoom`, `jump_camera_to_cell`, `clear_selection`, `select_pawn`, `right_click_cell`, then a `pos()` poll costing 1 × `list_colonists` (3.1 KB **[M]**) per iteration. | The camera and selection moves are deliberate — WANTED 0 territory, not waste. Migrate only the poll: `home/pawn_at`, or a `pawnIds` filter so `pos()` is not a full roster fetch per tick. | `Pawn.Position` | 0 (watchability) |

---

## 3. Remaining build order

Ranked by: is being wrong silent; does the path move the screen mid-stream; how
often it runs per turn; what it costs to build. Sizes are **[E]**.

### 6. `home/list_bills` (read) then `home/set_bill` (write). WANTED 4. Two to three days.

**SHIPPED** as one tool, `home/bills`, with `action` selecting read or write.

D-category and genuinely large. `Bill.ingredientFilter` is the filter WANTED 4
asks for, and `Bill_Production.ShouldDoNow()` is whether the bill will actually
run. Natural first user of WANTED 0's watchability: adding a bill selects the
workbench so a viewer sees it happen. Write side gets the dry-run mode with a
verified before and after.

### 9. `home/list_gizmos`. WANTED 12. Two days.

**SHIPPED** as `home/building_config`: the four named direct calls, plus a read-only gizmo listing behind `gizmos: true`.

Last because it is the most speculative — the value is in the write follow-
through (bed owner, medical bed, forbidden, power), and those four are probably
better as four named direct calls than a generic gizmo-firing surface that loses
dialogs.

### Not ranked, and why

- **WANTED 0, watchability** — a policy about writes, not a read path. Item 6 is its named first user.
- **WANTED 3, alerts/letters interrupt the agent** — control flow in `run.py`/`watch.py`; the reads it needs are already category B and already cheap **[M]**.
- **WANTED 5, prebuilt stockpile filters** — the read side exists in `zones.py`; the ask is a write.
- **WANTED 6, corpses** — a labelling and filtering change on `home/list_things`, which already walks them.
- **WANTED 9, answer a letter by choice** — a write, still untested because no choice letter has appeared.
- **WANTED 10, one batched status read** — see below.

### WANTED 10

The cost of a tool is surface area, not milliseconds: five entries in the
documentation are five chances to skip one, and that never shows up in a timing
log. Build it the way `home/list_pawns` works — one tool, opt-in blocks, full
granularity preserved as arguments; the reasoning is in `HANDOFF.md` and
`WANTED.md` item 10 and is not repeated here.

---

## 4. Already efficient — leave alone

Do not re-audit these next month.

**A — direct companion reads (13):** `home/ping`, `home/get_time`,
`home/list_pawns`, `home/list_things`, `home/list_buildings`,
`home/get_cells_plus`, `home/get_temperatures`, `home/list_zones`,
`home/zone_cells`, `home/trade`, `home/play_until_event`,
`home/place_building`, `home/research`.

**B — stock tools returning real structured data (11):**
`rimworld/get_game_info`, `list_messages`, `get_camera_state`, `list_letters`,
`get_ui_state`, `list_colonists`, `list_alerts`, `get_cell_info` /
`get_cells_info`, `list_architect_categories` / `list_architect_designators`,
`get_designator_state`, `take_screenshot`.

Measured **[M]**, whole-colony/whole-map, one call each, nothing on screen:

| Tool | Bytes | Seconds |
|---|---|---|
| `home/list_pawns {}` | 10 632 | 0.068 |
| `home/list_pawns {health:true}` | 28 083 | 0.066 |
| `home/list_pawns {equipment:true}` | 25 500 | 0.050 |
| `home/list_pawns {needs:true}` | 17 612 | 0.049 |
| `home/list_buildings {}` | 17 586 | 0.067 |
| `home/list_things {}` | 15 015 | 0.050 |
| `home/list_zones {}` | 11 554 | 0.049 |
| `home/get_time {}` | 2 228 | 0.050 |
| `rimworld/get_game_info` | 478 | 0.008 |
| `rimworld/list_messages` | 515 | 0.018 |
| `rimworld/get_camera_state` | 730 | 0.016 |
| `rimworld/list_letters` | 1 766 | 0.033 |
| `rimworld/get_ui_state` | 1 850 | 0.021 |
| `rimworld/list_colonists` | 3 084 | 0.020 |
| `rimworld/list_alerts` | 3 533 | 0.017 |
| `rimworld/get_ui_layout`, nothing open (floor) | 12 100 | — |

---

## Method, for whoever re-runs this

Call sites:

```bash
grep -rn "game(\s*[\"'][^\"']*[\"']" *.py | sed -E "s/.*game\(\s*[\"']([^\"']*)[\"'].*/\1/" | sort | uniq -c
```

then widened to `grep -o "\(rimworld\|home\)/[a-z_]*"` to catch the wrapped
calls (`pawns.all_pawns()`, `ui.click`) that the first pattern misses.

API names:

```bash
export DOTNET_ROLL_FORWARD=Major
"C:\Home\tools\ilspy\ilspycmd" -t RimWorld.Pawn_WorkSettings \
  "C:\Program Files (x86)\Steam\steamapps\common\RimWorld\RimWorldWin64_Data\Managed\Assembly-CSharp.dll"
```

per INSTALL.md's decompile section.
