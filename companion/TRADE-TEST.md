# `home/trade` — load test

Written 2026-09-01. **Built, installed and run the same night**, against a
caravan M spawned with dev mode on `Lampblack - day 37, the cold room`.
A real trade executed: 16 birdskin sold, 5 cloth bought, silver moved, verified
against the world rather than the payload. **The game was quit without saving.**

## Results — 2026-09-01, ~23:45, trader `Lyle` (bulk goods, Southwestern Esistan)

| Step | Check | Result |
|---|---|---|
| 0 | `list_traders` with no trader on the map | `success: true`, empty `traders[]`, negotiator picked (Longhoff, social 6, +9% price), `silverOnMapTotal: 443`, `orbitalTradeBeaconsPowered: 0`. An empty map is an answer, not a failure. |
| 0 | **the two silver numbers disagree** | with Lyle on the map: `silverOnMapTotal 443` vs `colonySilverForThisTrade` **0**. That is the control that matters — `inv.py`'s number and the deal's number are different questions, and the deal's is the one to spend against. (0 because the colony's silver stack is outside what the caravan deal counts.) |
| 1 | **no `Dialog_Trade` opens** | `rimworld/get_ui_state` after `open`: windows are `ImmediateWindow`, `MainTabWindow_Inspect`, `ImmediateWindow` — **no `RimWorld.Dialog_Trade`**, `nonImmediateDialogWindowOpen: false`. This is the whole point of the tool. |
| 1 | `rowCount` bigger than nine | **44 rows.** The UI path could reach about nine. |
| 1 | `minCount == -colonyCount` and `maxCount == traderCount` | **0 of 44 rows violate it.** |
| 1 | the silver row | present, `isCurrency: true`, `colonyCount: 0` — matching `colonySilverForThisTrade` from step 0 exactly |
| 1 | `negotiatorDistance` reported and `open` still succeeded | `negotiatorDistance: 12`, `success: true`. The game does not check distance and neither does this. |
| 2 | sign convention, live | `set Cloth +5` → `actionToDo: "PlayerBuys"`, `netSilverToColony: -10`. `set Leather_Bird -3` → `actionToDo: "PlayerSells"`, net moved to `-6`. **Opposite directions, correct signs.** |
| 3 | over-max count | **the expectation below was wrong** — see the correction under step 3. `set Cloth 999999` came back `ok: true` with `countToTransfer: 396`, clamped, and **the game did not pause** (tick and `paused` identical before and after). |
| 3 | cannot afford → `accept` | `success: false`, `errorKind: "cannot_afford"`, full `balance` echoed (`colonySilverAfter: -6`, `colonyCanAfford: false`), **no exception**, tick unchanged. `TradeDeal.TryExecute` was never called, so its null-dialog `FlashSilver()` never fired. |
| 3 | the silver row is not settable | `set Silver 5` → `ok: false`, "The silver row is computed from the other lines, not set." |
| 3 | **nothing pauses the game** | tick `2380357` and `paused: true` **identical before and after all four refusals**. This is the load-bearing control of the whole file. |
| 4 | `preview` | `wouldSucceed: true`, `staged[]` listing the sale, the purchase and the computed `+9` silver line |
| 4 | `accept` | `actuallyTraded: true`, `sessionActive: false`, `indicesInvalidated: true`, `moved[]` listing exactly what was staged, `traderResponse: []` |
| 4 | **verified against the world** | `home/list_things` before → after: cloth `ours 0 → 5`, birdskin `ours 26 → 10`, silver `ours 443 → 452`. Map totals unchanged for all three (396 / 26 / 1282), which is the independent check that the goods moved *between* colony and trader rather than into or out of the map. `inv.py cloth` shows the 5 at **(128,137)** — the trader's cell, exactly as documented. |
| 4 | cancel | re-`open` (44 rows again), stage 7 cloth, `cancel` → `sessionActive: false` and **all three counts unchanged**. Nothing moved. |
| 5 | **the fiasco case, reproduced deliberately** | M spawned the caravan *and* ordered a colonist to trade, so the vanilla `Dialog_Trade` opened on its own. `set` → `errorKind: "dialog_open"`; `accept` → `errorKind: "dialog_open"`; `sheet` still allowed (it is a read). |
| 5 | `close_dialog` | `dialogsFound: 1`, `dialogs[0]: {type: "RimWorld.Dialog_Trade", closed: true}`, `sessionWasActive: true`, `sessionActive: false`, `questReceived: false`, and `openWindows[]` with no `Dialog_Trade`. Independently confirmed by `rimworld/get_ui_state`. |
| 5 | `close_dialog` with nothing open | `success: true`, `dialogsFound: 0`, `sessionWasActive: false`. Not an error. |
| — | the **pawn-sale guard** | `isPawn` and `pawnDescription` are correct live on all four pawn rows in the sheet: `#14 Warg 1 → "animal Warg 1 (Warg)"` (ours, colonyCount 1) and `#15/#16/#17` the trader's turkeys and rooster. **Buying a pawn without the flag was allowed** (`set #15 1` → `PlayerBuys`), which is the intended asymmetry. |

### Two corrections to the procedure below, both found by running it

1. **An over-max count is CLAMPED, not refused.** Step 3's first row predicted
   `ok: false` with `countToTransfer` unchanged. What actually happens is that
   `Transferable.CanAdjustTo` **accepts** the value and `AdjustTo` clamps it to
   `maxCount` — `set Cloth 999999` staged 396. The guard is still doing its job:
   the reason it exists is that `AdjustTo` calls `Log.Error` on a value
   `CanAdjustTo` rejected, and `Log.Error` pauses the game. The tick was
   unchanged across the call, so nothing logged and nothing paused. Read that
   row as "the game does not pause", not as "the tool refuses".
2. **The pawn-sale guard's refusal branch was not reachable on this map.** The
   only colony-owned pawn row was our warg, and Lyle is a bulk goods trader
   whose `TraderWillTrade` is **false** for it — and that check runs *first*, so
   the line came back "This trader will not trade that thing" instead of the
   pawn refusal. That ordering is correct and should stay: if the trader will
   not take the pawn at all, telling the caller "pass allowPawns:true" would be
   a false promise. **So the `pawn_sale_refused` path itself is still untested
   live** — it needs a trader that buys pawns (a slaver, or any trader with a
   colony animal it will take). Everything it depends on (`isPawn`,
   `pawnDescription`, `WouldGiveAway`'s sign) was verified live.

### Still not run

- Selling to a trader that **will** take a pawn — the guard's own refusal path
  (see correction 2).
- An **ambiguous** `set` name. `"cloth"` resolved to a single row rather than
  several, so the `candidates[]` path never fired.
- `open` with `requireAdjacent: true` from a distance → `errorKind: "not_adjacent"`.
- `open` against a trader whose `canTradeNow` is false.
- **Gift mode**, still, in every respect.
- A trader carrying a quest, so `receiveQuest` / `questReceived: true` is
  exercised. Lyle had none.
- The claim that bought goods arrive **forbidden**: `inv.py` reported
  `0 forbidden` for the 5 cloth at the trader's cell. They are on the trader
  lord's `extraForbiddenThings`, which is a lord-level list rather than the
  thing's own `CompForbiddable.Forbidden`, so the two are not the same field —
  but the doc line below reads as if they were. Worth resolving.

---

Everything below is the original procedure, kept because most of it still needs
running on a different trader.

The tool source is `src\TradeTool.cs`. Its client is
`C:\Home\rimworld\instruments\trade.py`. The retired UI-clicking client is
`trade_ui_legacy.py` in the same folder.

Before any of this: build and install per `INSTALL.md` (RimWorld must be
**closed** for the copy; companions are discovered once, at bridge startup, so a
restart is required regardless), then confirm registration:

```python
rim.game('rimbridge/get_bridge_status')        # companions.diagnostics -> toolCount 9
rim.game('home/trade', {'action': 'status'})   # success:true, sessionActive:false
```

Both confirmed live 2026-09-01: `ToolClassCount: 9`, `ToolCount: 9`,
`Errors: []`, `Warnings: []`.

`games_tool_names` does **not** list companion tools (known gotcha, INSTALL.md).
Call `home/trade` by name through `rim.py` regardless.

---

## Which save, and the rule about saving

The current colony save is **`Lampblack - day 37, the cold room`**.

Whether a trader is present on it is **unknown as of this writing** — the last
recorded caravan (Aardvark, a shaman merchant) traded and left during the
2026-09-01 stream. Step 0 answers it.

If there is none, **ask M to spawn one** — she has dev mode and told
Fable on 2026-09-01 that she would rather do it herself than have us drive the
debug-action tree through her save. Step 0b is that ask; the debug-actions
route is kept below it as the fallback for when nobody is at the keyboard.

> ### However the trader gets there, DO NOT SAVE OVER THE COLONY
>
> A summoned caravan is a real mutation of a real save, whether M
> spawned it or a dev action did. Load `Lampblack - day 37, the cold room`,
> get the trader, test, and then **quit without saving**. If you want a
> reusable fixture, save under a different name (`trade test - <date>`) the
> moment the caravan arrives and work on that one. The one rule of this house
> is don't break her computer; the local corollary is don't break her colony.

---

## Step 0 — is there a trader?

```python
rim.game('home/trade', {'action': 'list_traders'})
```

or, more readably:

```
python "C:\Home\rimworld\instruments\trade.py"
```

Read four things out of the payload before going further:

| Field | What a correct answer looks like |
|---|---|
| `traders[]` | one row per trader pawn on the map, each with `id`, `traderKind`, `canTradeNow`, `position` |
| `negotiator` | a live colonist with `socialSkill` and `tradePriceImprovement`; **not null** |
| `traders[].colonySilverForThisTrade` | the silver the deal itself would count |
| `silverOnMapTotal` | every silver stack on the map, traders' included |

**The positive control that matters most here** is that those last two
*disagree* whenever a caravan is parked with silver in its pockets.
`silverOnMapTotal` is the number `inv.py` reports and PLAYBOOK rule 4a says is
wrong; `colonySilverForThisTrade` is the number to spend against. If they are
identical with a trader standing on the map holding silver, the ownership read
is broken and nothing below can be trusted.

If `traders` is empty and `orbitalTraders` is empty, go to step 0b.

## Step 0b — getting a trader onto the map

### Ask M first. This is the primary path.

She told Fable on 2026-09-01 that **she can spawn a trader caravan herself with
dev mode on**, and she would rather do that than have us drive the debug-action
tree through her save. So ask, and wait:

```powershell
python "C:\Home\tools\ask.py" "Fable's subagent has the trading tool built and wants a trader on the map to test against. Can you spawn one with dev mode?" --from fable --choices "spawned|wait|no" --timeout 540
```

Run it with a **tool timeout above `--timeout`** — 600000 ms against
`--timeout 540` — or the harness kills the window while she is still typing.
`ask.py` prints her answer on stdout and nothing else. Exit code 0 = answered,
2 = timed out, 3 = she closed the window.

| Answer | What it means |
|---|---|
| `spawned` | re-run step 0; if the caravan is still walking in from the map edge, run time (`python run.py 20`) until the arrival letter fires |
| `wait` | she is busy — try again in a few minutes, one re-ask, not a loop |
| `no`, or exit 2 / 3 | do **not** summon one yourself. Finish everything the tool can be tested on without a trader (steps 1 and 5 both have parts that work with an empty map — `open` against a nonexistent id, `close_dialog` with nothing open, `status`) and report the rest as untested |

The 540 is deliberate: the `ask.py` default is 280 because M asked for a
sub-five-minute timer so a sleeping human does not block a session forever.
Override it only when there is reason to think she is at the keyboard, which a
coordinator relaying a message from her is.

### Fallback: the debug-actions tool, when nobody is at the keyboard

Only if the ask timed out **and** the session has standing permission to
proceed unattended. Do **not** guess the debug-action path: they are
backslash-separated internal paths (`Dialog_Debug.GetNode`), they change
between builds, and `rimworld/search_debug_actions` exists precisely so nobody
has to hardcode one:

```python
rim.game('rimworld/search_debug_actions', {'query': 'trader caravan', 'limit': 20})
rim.game('rimworld/search_debug_actions', {'query': 'orbital trader', 'limit': 20})
rim.game('rimworld/search_debug_actions', {'query': 'incident', 'limit': 40})
```

Take the `path` string from a match whose execution metadata reports
`supported: true`, then:

```python
rim.game('rimworld/get_debug_action', {'path': '<path from the search>'})
rim.game('rimworld/execute_debug_action', {'path': '<path from the search>'})
```

Notes that will save a confused ten minutes, on either path:

- The incident that lands a caravan is `TraderCaravanArrival`; the orbital one
  is `OrbitalTraderArrival`. In the dev menu both usually sit behind an
  "Incidents" / "Execute incident" node that opens a **sub-list**, so
  `list_debug_action_children` on the parent is how you get from the category
  to the leaf.
- A caravan does not arrive instantly. It spawns at a map edge and walks in.
  Run time with `python run.py 20` (or `home/play_until_event`) until the
  arrival letter fires, then re-run step 0. This is true of M's spawn
  too — a `spawned` answer with an empty `traders[]` usually means it is still
  walking, not that it failed.
- An orbital ship needs a **powered comms console** to open comms in vanilla,
  and a **powered orbital trade beacon** for the colony to have anything to
  sell. `list_traders` reports `usableCommsConsoles` and
  `orbitalTradeBeaconsPowered`; if the beacon count is 0, an orbital sheet with
  an all-zero `OURS` column is correct, not broken.
- Dev mode itself may need enabling on our side. `rimworld/set_debug_setting`
  and `rimworld/get_designator_state` (which reports god mode) are the levers.
  If M spawned the trader from her own dev mode, none of that is needed.

---

## Step 1 — open a session, headless

```python
tr = rim.game('home/trade', {'action': 'list_traders'})
tid = tr['traders'][0]['id']
r = rim.game('home/trade', {'action': 'open', 'traderId': tid})
```

Expected: `success: true`, `sessionActive: true`, `rowCount` matching the
trader's stock plus whatever the colony can sell, and a `rows[]` array where
each row carries `label`, `defName`, `category`, `colonyCount`, `traderCount`,
`buyPrice`, `sellPrice`, `traderWillTrade`, `minCount`, `maxCount`.

**Controls, in order of how much they would hurt if they failed:**

1. **No `Dialog_Trade` opens.** Check with
   `rim.game('rimworld/get_ui_state')` — `windows[]` must contain no
   `RimWorld.Dialog_Trade`, and `nonImmediateDialogWindowOpen` must be
   unchanged. This is the whole point of the tool.
2. **`rowCount` is bigger than nine.** The UI path could reach about nine rows.
   If the sheet is short, compare it against the trader's inspect pane by hand.
3. **`minCount == -colonyCount` and `maxCount == traderCount`** on every row.
   That is the verified range rule; if a row violates it the sign convention has
   drifted and every number below is suspect.
4. **The silver row is present**, `isCurrency: true`, and its `colonyCount`
   equals `traders[].colonySilverForThisTrade` from step 0.
5. **`negotiatorDistance` is reported and `open` still succeeded** even when the
   negotiator is far away. That is the documented, verified behaviour: the game
   does not check distance. If `open` refuses without `requireAdjacent`, the
   guard is inverted.
6. Then the opposite control: re-run `open` with `requireAdjacent: true` from a
   distance and expect `errorKind: "not_adjacent"` with the measured distance.

## Step 2 — the sign convention, live

Pick a cheap thing the trader has and the colony does not.

```python
rim.game('home/trade', {'action': 'set', 'item': '<defName>', 'count': 5})
```

Expected: `lines[0].ok: true`, `countToTransfer: 5`,
`actionToDo: "PlayerBuys"`, and in `balance`, `netSilverToColony` **negative**
and `colonySilverAfter` = `colonySilverNow` - 5x`buyPrice` (rounded by
`CostToInt`).

Then the mirror, on something the colony owns:

```python
rim.game('home/trade', {'action': 'set', 'item': '<defName>', 'count': -3})
```

Expected `actionToDo: "PlayerSells"` and `netSilverToColony` moving the other
way. **If buy and sell come out swapped, stop** — `CountToTransfer > 0 means
the colony buys` is the load-bearing claim of the whole tool.

## Step 3 — the three refusals, each of which must NOT pause the game

Note the tick before and after each of these
(`rim.game('home/get_time')['ticksGame']`, or `rimworld/get_game_info`) and
confirm the game's paused state is **unchanged**. Two of the three would pause
the game if the guards were removed.

| Try this | Expect | Why it matters |
|---|---|---|
| `set` a count above `maxCount` (e.g. buy 999999 of something the trader has 20 of) | `lines[0].ok: false`, an `error`, `minCount`/`maxCount` echoed, `countToTransfer` **unchanged** | `Transferable.AdjustTo` calls `Log.Error` on a rejected value and `Log.Error` calls `TickManager.Pause()`. The tool checks `CanAdjustTo` first so this never happens. A game that pauses here means the guard is gone. |
| Stage a purchase the colony cannot afford, then `accept` | `success: false`, `errorKind: "cannot_afford"`, `balance` and `staged` echoed, **no exception** | `TradeDeal.TryExecute`'s cannot-afford branch is `Find.WindowStack.WindowOfType<Dialog_Trade>().FlashSilver()` with no null check. Headless that is a NullReferenceException. The tool evaluates the same predicate first and never calls `TryExecute`. |
| `open` against a trader whose `canTradeNow` is false (a downed or departed one) | `errorKind: "cannot_trade_now"` | `TradeSession.SetupWith` calls `Log.Warning` in that case, which can pop the log window open on a streaming screen. |

Also worth one call each:

- `set` with a name that matches several rows (e.g. a def with quality variants)
  → `error` naming the count and a `candidates[]` list; then re-address it as
  `"#<index>"` and expect it to work.
- `set` on a row where `traderWillTrade` is false → a clean per-line refusal.

## Step 4 — preview, then accept

```python
rim.game('home/trade', {'action': 'preview'})
rim.game('home/trade', {'action': 'accept'})
```

Expected from `accept`: `actuallyTraded: true`, `moved[]` listing exactly what
was staged, `sessionActive: false`, `indicesInvalidated: true`, and
`traderResponse[]` carrying any transient message the game raised during the
call.

**Then verify against the world, not the payload:**

```
python "C:\Home\rimworld\instruments\inv.py" silver
python "C:\Home\rimworld\instruments\inv.py" --forbidden
```

Two things to expect and not misread:

- Bought goods are placed at the **trader's** cell, not the negotiator's, and
  are added to the trader lord's `extraForbiddenThings` — so they sit on the
  ground next to the caravan, **forbidden**, until the caravan leaves. That is
  vanilla and it is the same whether a human or this tool did the trade. A
  purchase that "did not arrive in the stockpile" is this, not a bug.
- `accept` calls `TradeSession.Close()` and the deal was already `Reset()` by
  `TryExecute`, so every row index from the sheet is dead. Re-`open` to trade
  again.

Negative control worth doing once: `open`, stage something, then `cancel`, and
confirm `inv.py` shows **nothing moved** and `status` reports
`sessionActive: false`.

## Step 5 — `close_dialog`, the fiasco case

This is the one action that exists because of an actual incident: a previous
session left a trade dialog open on screen and everything downstream ate
clicks while reporting success.

Reproduce it deliberately:

1. In the game, right-click a trader with a colonist selected and let them walk
   over so the vanilla `Dialog_Trade` opens (or use
   `rimworld/open_context_menu` + `execute_context_menu_option`).
2. `rim.game('home/trade', {'action': 'set', ...})` → expect
   `errorKind: "dialog_open"` and **no mutation**. Same for `open` and
   `accept`.
3. `rim.game('home/trade', {'action': 'close_dialog'})` → expect
   `dialogsFound: 1`, `dialogs[0].closed: true`, `sessionWasActive: true`,
   `sessionActive: false` afterwards, and an `openWindows[]` list with no
   `Dialog_Trade` in it.
4. `rimworld/get_ui_state` as the independent check.

A detail to watch for: `Dialog_Trade.Close` hands over a trader's quest when
the trader has one (`TradeUtility.ReceiveQuestFromTrader`), and `close_dialog`
goes through `Window.Close` rather than `WindowStack.TryRemove` specifically so
that still happens. If the trader had `hasQuest: true` in `list_traders`,
expect a quest letter after the close. Pass `receiveQuest: false` to suppress
it.

---

## What is NOT covered by any of the above

- **Gift mode.** `giftMode: true` flips `PositiveCountDirection` to
  `Destination`, which inverts the sign convention for that session. The code
  handles it and the payload reports `giftMode`, but no control above tests it.
  Test it separately or do not use it.
- **The player as a caravan.** `Dialog_Trade` has a whole
  `playerIsCaravan` branch (mass, tiles per day, days of food) that this tool
  does not implement at all. Colony-side trading only.
- ~~**Selling pawns.**~~ **Guarded since 2026-09-01.**
  `TradeUtility.AllSellableColonyPawns` feeds the sheet, so colonists,
  prisoners and colony animals appear as ordinary rows — and this tool
  addresses rows by fuzzy name, which makes `sell finn 1` a plausible typo with
  an irreversible result. `set` now refuses any line that would hand a pawn
  over unless `allowPawns: true` is passed, and names the row it refused.
  Every sheet row carries `isPawn` and `pawnDescription` so a caller can see it
  coming. **Buying is never blocked** — buying animals from a trader is
  ordinary play, and this colony's tame warg came from a war merchant. The
  guard follows the deal's direction rather than the raw sign, so in gift mode
  it is the positive counts that are refused. `trade.py` mirrors it as
  `--allow-pawns`.
- **`Ideology` refusals.** `TryExecute` can return false with
  `actuallyTraded: false` when an ideo forbids trading organs. The payload
  reports it via `traderResponse`, untested.
