"""Source guards for home/grid, the coordinate-grid overlay.

The drawing happens in a Harmony patch on a per-frame OnGUI method, which no
offline test can execute. What CAN be checked here is the set of decisions that
were expensive to get right and are silent when they rot: the patch position,
the guards a Prefix has to replicate, the cell-to-screen arithmetic, and the two
Unity types this csproj cannot reference.
"""
from pathlib import Path
import unittest


SRC = Path(__file__).parents[1] / "src"
GRID = (SRC / "GridOverlayTool.cs").read_text(encoding="utf-8")
CSPROJ = (SRC / "HomeBridge.BridgeTools.csproj").read_text(encoding="utf-8")

# The comments name the wrong turns on purpose ("WorldRenderedNow does NOT
# exist"), so a check that a name is absent has to read the code without them.
CODE = "\n".join(l for l in GRID.splitlines()
                 if not l.lstrip().startswith("//"))


class GridToolSourceTests(unittest.TestCase):
    def test_the_tool_is_registered_under_its_own_name(self):
        self.assertIn('private const string ToolName = "home/grid"', GRID)

    def test_the_write_is_a_dry_run_by_default(self):
        self.assertIn("bool dryRun = true", GRID)
        self.assertIn("var applied = !dryRun && !wanted.SameAs(before);", GRID)

    def test_omitting_a_field_leaves_it_alone_so_no_arguments_is_a_read(self):
        """Every write field is nullable or has a 0 sentinel: the SDK binder
        cannot tell an absent bool from a false one, so a plain bool here would
        turn the grid off on every read."""
        self.assertIn("bool? enabled = null", GRID)
        self.assertIn("bool? labels = null", GRID)
        self.assertIn("string color = null", GRID)
        self.assertIn("int step = 0", GRID)
        self.assertIn("double alpha = 0", GRID)


class GridPatchSourceTests(unittest.TestCase):
    def test_it_patches_the_before_main_tabs_hook(self):
        self.assertIn('"MapInterfaceOnGUI_BeforeMainTabs"', GRID)

    def test_it_is_a_prefix_so_the_grid_lands_under_the_map_ui(self):
        """MapInterfaceOnGUI_BeforeMainTabs draws the colonist bar, the gizmos
        and the resource readout at its END. IMGUI is painter's-algorithm, so a
        Postfix would put the grid on top of all three."""
        self.assertIn("prefix: new HarmonyMethod(", GRID)
        self.assertNotIn("postfix: new HarmonyMethod(", GRID)

    def test_a_prefix_replicates_the_guards_it_runs_ahead_of(self):
        self.assertIn("var map = Find.CurrentMap;", GRID)
        self.assertIn("if (map == null || !WorldRendererUtility.DrawingMap)", GRID)

    def test_the_world_render_check_uses_the_name_that_exists_in_1_6(self):
        """WorldRenderedNow is not a member of WorldRendererUtility in 1.6."""
        self.assertNotIn("WorldRenderedNow", CODE)

    def test_a_missing_patch_target_is_reported_not_thrown(self):
        self.assertIn("_patchError = ex.GetType().Name", GRID)
        self.assertIn('["installed"] = owners.Contains(HarmonyId)', GRID)

    def test_the_disabled_path_is_one_read_and_a_return(self):
        body = GRID[GRID.index("internal static void Prefix()"):]
        body = body[:body.index("catch (Exception ex)")]
        first = body.index("Volatile.Read(ref _settings)")
        guard = body.index("!settings.Enabled")
        draw = body.index("Draw(settings)")
        self.assertLess(first, guard)
        self.assertLess(guard, draw)

    def test_a_failed_patch_can_be_retried(self):
        """_patched is put back to 0 in the catch, or a transient failure would
        be reported forever without anything having tried twice."""
        catch = GRID[GRID.index("_patchError = ex.GetType().Name"):]
        self.assertIn("Interlocked.Exchange(ref _patched, 0)",
                      catch[:catch.index("}")])

    def test_switching_the_grid_on_clears_the_error_budget(self):
        self.assertIn("Volatile.Write(ref _errors, 0)", GRID)

    def test_nothing_escapes_the_draw_catch(self):
        """A Harmony patch that throws surfaces as Log.Error, and Log.Error
        calls TickManager.Pause() -- the colony would stop with nothing saying
        why."""
        self.assertIn("catch (Exception ex)", GRID)
        self.assertIn("Interlocked.Increment(ref _errors) >= ErrorBudget", GRID)


class GridDrawingSourceTests(unittest.TestCase):
    def test_the_screen_conversion_matches_gen_map_ui(self):
        self.assertIn("camera.WorldToScreenPoint(new Vector3(x, 0f, z)) / scale",
                      GRID)
        self.assertIn("new Vector2(v.x, UI.screenHeight - v.y)", GRID)

    def test_the_view_rect_is_treated_as_inclusive(self):
        self.assertIn("view.maxX + 1", GRID)
        self.assertIn("view.maxZ + 1", GRID)

    def test_the_grid_is_clamped_to_the_map(self):
        self.assertIn("Math.Min(map.Size.x, view.maxX + 1)", GRID)
        self.assertIn("Math.Min(map.Size.z, view.maxZ + 1)", GRID)

    def test_density_is_capped_so_zooming_out_cannot_draw_thousands_of_lines(self):
        self.assertIn("(x1 - x0) / drawStep > MaxLines", GRID)
        self.assertIn("(x1 - x0) / labelStep > MaxLabelColumns", GRID)

    def test_the_ui_scale_divisor_can_never_be_zero(self):
        self.assertIn("if (scale <= 0f)", GRID)

    def test_labels_are_drawn_with_the_games_own_world_space_text(self):
        self.assertIn("GenMapUI.DrawText(new Vector2(x, z)", GRID)

    def test_the_two_imgui_types_this_csproj_cannot_reference_are_unused(self):
        """Widgets.Label and UnityEngine.Event both drag in
        UnityEngine.IMGUIModule, which the csproj does not reference; using
        either is a compile error, not a runtime surprise."""
        self.assertNotIn("UnityEngine.IMGUIModule", CSPROJ)
        self.assertNotIn("Widgets.Label(", CODE)
        self.assertNotIn("Event.current", CODE)
        self.assertNotIn("GUI.color", CODE)

    def test_a_label_never_names_a_cell_off_the_edge_of_the_map(self):
        """x1/z1 is a BOUNDARY, not a cell: on a 250-wide map with the east edge
        in view it is 250 and the cells run 0..249. The LINE loops are inclusive
        of it on purpose -- a line at 250 is the east wall of cell 249 -- but an
        inclusive label loop drew "250,250", a coordinate no tool accepts."""
        block = GRID[GRID.index("if (settings.Labels)\n            {\n                // STRICTLY"):]
        block = block[:block.index("Volatile.Write(ref _lines, lines)")]
        self.assertIn("for (var x = FirstMultiple(x0, labelStep); x < x1; x += labelStep)",
                      block)
        self.assertIn("for (var z = FirstMultiple(z0, labelStep); z < z1; z += labelStep)",
                      block)
        self.assertNotIn("x <= x1", block)
        self.assertNotIn("z <= z1", block)

    def test_the_line_loops_stay_inclusive_of_the_far_boundary(self):
        block = GRID[GRID.index("var lines = 0;"):GRID.index("var labelStep = drawStep;")]
        self.assertIn("for (var x = FirstMultiple(x0, drawStep); x <= x1; x += drawStep)",
                      block)
        self.assertIn("for (var z = FirstMultiple(z0, drawStep); z <= z1; z += drawStep)",
                      block)

    def test_the_last_real_cell_can_still_carry_a_label(self):
        """`< x1` is the fix and `< x1 - 1` would be an overcorrection: the cell
        at x1-1 exists, so it must still pass whenever it falls on the step."""
        self.assertNotIn("x < x1 - 1", GRID)
        self.assertNotIn("z < z1 - 1", GRID)
        first = eval(  # FirstMultiple, transcribed
            "lambda frm, step: 0 if frm <= 0 else ((frm + step - 1) // step) * step")
        # A 251-wide map, whole map in view, step 10: 250 is a real cell and the
        # last label; on a 250-wide map the last label is 240, not 250.
        for width, step, expected_last in ((251, 10, 250), (250, 10, 240), (250, 1, 249)):
            labels = list(range(first(0, step), width, step))
            self.assertEqual(expected_last, labels[-1])
            self.assertLess(labels[-1], width)


if __name__ == "__main__":
    unittest.main()
