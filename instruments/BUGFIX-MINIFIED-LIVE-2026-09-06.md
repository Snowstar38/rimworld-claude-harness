# Packed furniture: live Python workaround

**ROLLED BACK IN FULL at M's request during the stream.** `buildings.py`
and its original tests are restored byte-for-byte to their pre-patch Git HEAD
versions. The added PLAYBOOK instruction is removed. All 30 original tests pass.
Minified support is deferred; the notes below describe the abandoned patch,
not current behavior. Its test file is archived in `C:/Home/tmp/`.

## Live correction

M reported repeated selection without Reinstall firing on installed
furniture after the patch. The new selection loop, cancellation, identity checks,
and exact-label preference are now restricted to MinifiedThing targets. Installed
buildings again use their original clear-selection, single-click, list, execute
sequence. An offline regression test asserts that full call sequence. The first
patch applied the new selection logic too broadly; its offline fixtures did not
establish compatibility with installed furniture's live IDs.

M requested a fix while streaming, without restarting RimWorld. Changed
`buildings.py gizmo` and the PLAYBOOK; no DLL deployment or service/game restart.

`home/building_config` rejects MinifiedThing because its resolver searches
buildings. The gizmo helper now falls back to `get_map_target_info` for an exact
spawned minified ID. Read-only live checks resolved both sculptures:
`Thing_MinifiedThing427542` at 114,151 and `Thing_MinifiedThing441092` at 113,152.

Use `python buildings.py gizmo <exact-ID> "Install" --do`. Install arms a placement
tool; Hands still chooses/clicks the destination and verifies pending construction.
`inv.py minified --json` finds IDs; its aggregated label is not a per-item label.
The plural `gizmos` command still uses the building-only read.

Selection refuses an active/unreadable designator. Use `python act.py clear` first
if needed. After clearing object selection, one right-click cancels a remaining
ability/pawn targeter; without a selected pawn it cannot issue a pawn order.
Then at most eight left clicks cycle the cell, checking for exactly the requested
ID after each. Selection is checked again before executing the gizmo; the bridge
also rejects stale selection-scoped gizmo IDs. Exact label matches take priority
over substrings, so Install does not ambiguously match Uninstall.

Sol verified cycling and right-click cancellation against decompiled methods in
the installed game assembly: Selector.SelectUnderMouse, MapInterface.HandleMapClicks,
DesignatorManager.ProcessInputEvents and Targeter.ProcessInputEvents.

Validation: 38 offline tests passed (`python -m unittest test_minified_gizmo
test_buildings_cli`), plus read-only live target resolution. No live clicks,
selection changes, gizmo execution, or placement were performed during repair,
because the stream's Hands controller was playing. End-to-end use remains for
Hands. Python CLI processes load the fix on their next invocation.
