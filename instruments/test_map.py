"""Offline regressions for map.py glyphs, layer names and rectangle forms.

Every test mocks the bridge; nothing here starts or touches RimWorld.
"""
import io
import unittest
from unittest import mock

import map as rimmap


def thing(defName, className="Building", **kw):
    d = {"defName": defName, "className": className, "label": defName}
    d.update(kw)
    return d


def cell(x, z, things=(), terrain="Soil", roof=None, **kw):
    c = {"x": x, "z": z, "walkable": True, "terrainDefName": terrain,
         "things": list(things)}
    if roof:
        c["roofDefName"] = roof
    c.update(kw)
    return c


def grid_of(cells):
    return {(c["x"], c["z"]): c for c in cells}


class BucketTests(unittest.TestCase):
    """Natural rock, smoothed rock, ore vein and a built wall are four kinds."""

    def test_natural_rock_defs_are_rock(self):
        for dn in ("Granite", "Sandstone", "Slate", "Limestone", "Marble",
                   "CollapsedRocks"):
            self.assertEqual("rock", rimmap.bucket(thing(dn, "Mineable")), dn)

    def test_natural_rock_survives_a_payload_with_no_classname(self):
        self.assertEqual("rock", rimmap.bucket({"defName": "Granite"}))

    def test_smoothed_rock_wall_is_its_own_bucket_not_a_built_wall(self):
        self.assertEqual("smoothrock",
                         rimmap.bucket(thing("SmoothedGranite", "Mineable")))

    def test_built_wall_is_a_wall(self):
        self.assertEqual("wall", rimmap.bucket(thing("Wall", "Building")))

    def test_ore_defs_are_veins(self):
        for dn in ("MineableSteel", "MineableComponentsIndustrial",
                   "MineablePlasteel", "MineableGold", "MineableSilver",
                   "MineableUranium", "MineableJade"):
            self.assertEqual("vein", rimmap.bucket(thing(dn, "Mineable")), dn)

    def test_every_struct_bucket_is_accounted_for_in_layer_1(self):
        self.assertIn("smoothrock", rimmap.STRUCT)


class OreGlyphTests(unittest.TestCase):
    def test_steel_and_machinery_are_different_characters(self):
        self.assertNotEqual(rimmap.ore_glyph("MineableSteel"),
                            rimmap.ore_glyph("MineableComponentsIndustrial"))

    def test_every_listed_ore_has_a_unique_glyph(self):
        glyphs = [g for g, _dn, _d in rimmap.ORE_KIND]
        self.assertEqual(len(glyphs), len(set(glyphs)))
        self.assertNotIn("%", glyphs)

    def test_an_unlisted_ore_falls_back_to_percent(self):
        self.assertEqual("%", rimmap.ore_glyph("MineableUnobtainium"))

    def test_ore_glyphs_do_not_collide_with_the_other_layer_1_glyphs(self):
        others = {"#", "S", "H", "/", ".", ",", "-", "=", "~", "?", " ",
                  "@", "d", "X", "v", "u"}
        for g, _dn, _d in rimmap.ORE_KIND:
            self.assertNotIn(g, others, g)


class Layer1Tests(unittest.TestCase):
    def render(self, cells):
        grid = grid_of(cells)
        things, *_ = rimmap.collect(grid)
        xs = [c["x"] for c in cells]
        zs = [c["z"] for c in cells]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rimmap.layer1(min(xs), min(zs), max(xs) + 1, max(zs) + 1,
                          grid, things)
        return out.getvalue()

    def test_rock_and_wall_draw_different_glyphs(self):
        txt = self.render([cell(0, 0, [thing("Granite", "Mineable")]),
                           cell(1, 0, [thing("Wall", "Building")])])
        row = [l for l in txt.splitlines() if l.startswith("   0 ")][0]
        self.assertEqual("#H", row[5:7])

    def test_smoothed_rock_wall_draws_S(self):
        txt = self.render([cell(0, 0, [thing("SmoothedGranite", "Mineable")])])
        row = [l for l in txt.splitlines() if l.startswith("   0 ")][0]
        self.assertEqual("S", row[5:6])

    def test_each_ore_draws_its_own_glyph(self):
        txt = self.render([
            cell(0, 0, [thing("MineableSteel", "Mineable")]),
            cell(1, 0, [thing("MineableComponentsIndustrial", "Mineable")]),
            cell(2, 0, [thing("MineableGold", "Mineable")]),
        ])
        row = [l for l in txt.splitlines() if l.startswith("   0 ")][0]
        self.assertEqual("skg", row[5:8])

    def test_the_footer_counts_each_ore_def_by_name(self):
        txt = self.render([cell(0, 0, [thing("MineableSteel", "Mineable")]),
                           cell(1, 0, [thing("MineableSteel", "Mineable")]),
                           cell(2, 0, [thing("MineableGold", "Mineable")])])
        self.assertIn("s:MineableSteel x2", txt)
        self.assertIn("g:MineableGold x1", txt)

    def test_an_unlisted_ore_is_named_in_the_footer(self):
        txt = self.render([cell(0, 0, [thing("MineableUnobtainium",
                                             "Mineable")])])
        self.assertIn("MineableUnobtainium", txt)
        self.assertIn("add it to ORE_KIND", txt)

    def test_the_layer_says_which_glyph_is_built_and_which_is_natural(self):
        txt = self.render([cell(0, 0, [thing("Wall", "Building")])])
        self.assertIn("NATURAL", txt)
        self.assertIn("CONSTRUCTED", txt)


class Layer5Tests(unittest.TestCase):
    def render(self, cells):
        grid = grid_of(cells)
        things, _n, _c, _u, _d, forbid = rimmap.collect(grid)
        xs = [c["x"] for c in cells]
        zs = [c["z"] for c in cells]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rimmap.layer5(min(xs), min(zs), max(xs) + 1, max(zs) + 1,
                          grid, things, forbid)
        return out.getvalue()

    def test_natural_rock_is_not_drawn_as_a_wall_on_the_buildings_layer(self):
        cells = [cell(x, 0, [thing("Granite", "Mineable")]) for x in range(14)]
        cells += [cell(x, 0, [thing("Wall", "Building")])
                  for x in range(14, 28)]
        txt = self.render(cells)
        row = [l for l in txt.splitlines() if l.startswith("   0 ")][0]
        self.assertEqual("#" * 14 + "H" * 14, row[5:33])

    def test_ore_and_smoothed_rock_have_their_own_context_glyphs(self):
        cells = [cell(0, 0, [thing("MineableSteel", "Mineable")]),
                 cell(1, 0, [thing("SmoothedGranite", "Mineable")])]
        cells += [cell(x, 0, [thing("Wall", "Building")]) for x in range(2, 16)]
        txt = self.render(cells)
        row = [l for l in txt.splitlines() if l.startswith("   0 ")][0]
        self.assertEqual(":S", row[5:7])

    def test_rock_stays_context_so_a_sparse_layer_still_compacts(self):
        cells = [cell(x, 0, [thing("Granite", "Mineable")]) for x in range(20)]
        cells.append(cell(20, 0, [thing("Wall", "Building")]))
        txt = self.render(cells)
        self.assertIn("COMPACTED", txt)

    def test_a_compacted_layer_still_counts_the_natural_rock(self):
        cells = [cell(x, 0, [thing("Granite", "Mineable")]) for x in range(20)]
        cells.append(cell(20, 0, [thing("Wall", "Building")]))
        txt = self.render(cells)
        self.assertIn("natural rock in these bounds: 20 '#'", txt)

    # ---- turn 13: a berry bush on a build target was invisible here --------
    def test_a_berry_bush_is_drawn_on_the_build_layer(self):
        cells = [cell(x, 0, [thing("Plant_Berry", "Plant")]) for x in range(14)]
        cells += [cell(x, 0, [thing("Wall", "Building")]) for x in range(14, 28)]
        txt = self.render(cells)
        row = [l for l in txt.splitlines() if l.startswith("   0 ")][0]
        self.assertEqual("*" * 14 + "H" * 14, row[5:33])
        self.assertIn("PLANTS on cells with nothing built", txt)

    def test_a_plant_never_wears_a_building_character(self):
        """'b' is a bed here and 'c' a container, so the plants take '*'/'^'."""
        build = {s for s, _ in rimmap.L5KEY}
        for _kind, sym in rimmap.L5PLANT:
            if sym in ("T", '"', "h"):
                continue                       # free characters on this layer
            self.assertNotIn(sym, {"b", "B", "c", "t", "s", "w", "f", "e",
                                   "H", "/", "-", "+", "!", "%"})
        self.assertIn("*", build)
        self.assertIn("^", build)

    def test_a_tree_and_a_crop_are_drawn_apart(self):
        cells = [cell(0, 0, [thing("Plant_TreeOak", "Plant")]),
                 cell(1, 0, [thing("Plant_Potato", "Plant")]),
                 cell(2, 0, [thing("Plant_Healroot", "Plant")])]
        cells += [cell(x, 0, [thing("Wall", "Building")]) for x in range(3, 17)]
        txt = self.render(cells)
        row = [l for l in txt.splitlines() if l.startswith("   0 ")][0]
        self.assertEqual("T^h", row[5:8])

    def test_grass_is_context_so_the_layer_still_compacts(self):
        cells = [cell(x, 0, [thing("Plant_Grass", "Plant")]) for x in range(20)]
        cells.append(cell(20, 0, [thing("Wall", "Building")]))
        txt = self.render(cells)
        self.assertIn("COMPACTED", txt)

    def test_a_plant_under_a_building_is_counted_not_drawn(self):
        cells = [cell(0, 0, [thing("Wall", "Building"),
                             thing("Plant_Berry", "Plant")])]
        cells += [cell(x, 0, [thing("Wall", "Building")]) for x in range(1, 15)]
        txt = self.render(cells)
        self.assertIn("1 further plant cell(s) already carry a building", txt)


class LegendTests(unittest.TestCase):
    def legend(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rimmap.full_legend()
        return out.getvalue()

    def test_the_legend_names_natural_rock_against_a_built_wall(self):
        txt = self.legend()
        self.assertIn("NATURAL ROCK AND A BUILT WALL ARE NEVER THE SAME"
                      " CHARACTER", txt)

    def test_the_legend_says_the_items_layer_draws_plants(self):
        self.assertIn("`--layers items` DOES DRAW PLANTS", self.legend())

    def test_the_legend_lists_every_ore(self):
        txt = self.legend()
        for _g, _dn, desc in rimmap.ORE_KIND:
            self.assertIn(desc, txt)


class SizeParsingTests(unittest.TestCase):
    def test_one_number_is_a_square(self):
        argv = ["112", "140", "--size", "96"]
        self.assertEqual((96, 96), rimmap.take_size(argv))
        self.assertEqual(["112", "140"], argv)

    def test_two_numbers_are_width_and_height(self):
        argv = ["112", "140", "--size", "96", "48"]
        self.assertEqual((96, 48), rimmap.take_size(argv))
        self.assertEqual(["112", "140"], argv)

    def test_a_size_before_the_coordinates_does_not_eat_one_of_them(self):
        argv = ["--size", "96", "112", "140"]
        self.assertEqual((96, 96), rimmap.take_size(argv))
        self.assertEqual(["112", "140"], argv)

    def test_no_size_flag_is_the_default_square(self):
        self.assertEqual((rimmap.SIZE, rimmap.SIZE), rimmap.take_size(["1"]))

    def test_a_size_with_no_number_is_refused(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertIsNone(rimmap.take_size(["--size", "--full"]))


class MainArgumentTests(unittest.TestCase):
    """--layers validation, --legend, and the two rectangle conventions."""

    def run_main(self, argv, size=(200, 200)):
        drew = {}

        def fake_scan(x0, z0, x1, z1, **kw):
            drew["bounds"] = (x0, z0, x1, z1)
            return {}

        with mock.patch.object(rimmap.rim, "init"), \
             mock.patch.object(rimmap.rim, "map_size", return_value=size), \
             mock.patch.object(rimmap, "scan", side_effect=fake_scan), \
             mock.patch.object(rimmap, "colonists", return_value={}), \
             mock.patch.object(rimmap, "fetch_rooms", return_value=None), \
             mock.patch.object(rimmap, "hostile_alerts", return_value=[]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = rimmap.main(argv)
        return rc, out.getvalue(), drew.get("bounds")

    # ---- bug 3: unknown layer names are refused before anything is drawn ----
    def test_an_unknown_layer_name_exits_2_and_draws_nothing(self):
        rc, txt, bounds = self.run_main(["112", "140", "--layers", "structure"])
        self.assertEqual(2, rc)
        self.assertIsNone(bounds)
        self.assertNotIn("== LAYER", txt)
        self.assertIn("structure", txt)

    def test_the_refusal_lists_the_valid_names(self):
        _rc, txt, _b = self.run_main(["112", "140", "--layers", "structure"])
        for name in rimmap.LAYER_NAMES.values():
            self.assertIn(name, txt)

    def test_one_bad_name_among_good_ones_is_still_refused(self):
        rc, txt, bounds = self.run_main(["112", "140",
                                         "--layers", "build,structure"])
        self.assertEqual(2, rc)
        self.assertIsNone(bounds)
        self.assertNotIn("== LAYER", txt)

    def test_build_is_a_real_layer_name(self):
        rc, _txt, bounds = self.run_main(["112", "140", "--layers", "build"])
        self.assertEqual(0, rc)
        self.assertIsNotNone(bounds)

    def test_beds_still_aliases_the_buildings_layer(self):
        self.assertEqual(5, rimmap.LAYER_ALIAS["beds"])

    # ---- bug 4: --legend prints the legend AS WELL AS the map --------------
    def test_legend_prints_the_map_too(self):
        rc, txt, bounds = self.run_main(["112", "140", "--legend"])
        self.assertEqual(0, rc)
        self.assertIsNotNone(bounds)
        self.assertIn("== LAYER 1", txt)
        self.assertIn("== LEGEND", txt)
        self.assertLess(txt.index("== LAYER 1"), txt.index("== LEGEND"))

    def test_legend_only_prints_the_legend_and_never_calls_the_bridge(self):
        rc, txt, bounds = self.run_main(["--legend-only"])
        self.assertEqual(0, rc)
        self.assertIsNone(bounds)
        self.assertIn("LAYER 1: terrain / roof", txt)
        self.assertNotIn("== LAYER 1:", txt)

    def test_legend_with_no_coordinates_is_the_legend_alone(self):
        rc, txt, bounds = self.run_main(["--legend"])
        self.assertEqual(0, rc)
        self.assertIsNone(bounds)
        self.assertIn("LAYER 1: terrain / roof", txt)

    # ---- bug 5: sizes ------------------------------------------------------
    def test_four_positional_numbers_are_x_z_width_height(self):
        _rc, _txt, bounds = self.run_main(["96", "120", "48", "32"])
        self.assertEqual((96, 120, 144, 152), bounds)

    def test_corner_reads_the_second_pair_as_an_opposite_corner(self):
        _rc, _txt, bounds = self.run_main(["96", "120", "150", "170",
                                           "--corner"])
        self.assertEqual((96, 120, 150, 170), bounds)

    def test_rect_is_still_the_corner_form(self):
        _rc, _txt, bounds = self.run_main(["96", "120", "150", "170", "--rect"])
        self.assertEqual((96, 120, 150, 170), bounds)

    def test_size_w_h_makes_a_rectangle_around_the_centre(self):
        _rc, _txt, bounds = self.run_main(["112", "140", "--size", "96", "48"])
        self.assertEqual((64, 116, 160, 164), bounds)

    def test_an_empty_window_is_refused_naming_both_forms(self):
        rc, txt, bounds = self.run_main(["96", "120", "0", "10"])
        self.assertEqual(2, rc)
        self.assertIsNone(bounds)
        self.assertIn("EMPTY window", txt)
        self.assertIn("--size W H", txt)
        self.assertIn("x z WIDTH HEIGHT", txt)

    def test_a_window_entirely_off_the_map_is_refused_not_silent(self):
        rc, txt, _b = self.run_main(["900", "900", "10", "10"], size=(200, 200))
        self.assertEqual(2, rc)
        self.assertIn("nothing left after clamping", txt)
        self.assertIn("x z WIDTH HEIGHT", txt)

    def test_corner_with_only_two_numbers_is_refused_not_a_crash(self):
        rc, txt, bounds = self.run_main(["96", "120", "--corner"])
        self.assertEqual(2, rc)
        self.assertIsNone(bounds)
        self.assertIn("four numbers", txt)

    def test_two_numbers_still_centre_the_default_square(self):
        _rc, _txt, bounds = self.run_main(["112", "140"])
        h = rimmap.SIZE // 2
        self.assertEqual((112 - h, 140 - h, 112 - h + rimmap.SIZE,
                          140 - h + rimmap.SIZE), bounds)

    def test_no_arguments_prints_the_usage(self):
        rc, txt, _b = self.run_main([])
        self.assertEqual(0, rc)
        self.assertIn("--layers items` DOES DRAW PLANTS", txt)

    # ---- turn 3: a real command answered with the whole manual -------------
    def test_corner_with_no_numbers_is_an_error_not_the_manual(self):
        rc, txt, bounds = self.run_main(["--corner"])
        self.assertEqual(2, rc)
        self.assertIsNone(bounds)
        self.assertIn("four numbers", txt)
        self.assertNotIn("MERGED DEFAULT VIEW", txt)

    def test_unreadable_coordinates_are_named_not_swallowed(self):
        rc, txt, bounds = self.run_main(["x", "z", "--corner", "a", "b"])
        self.assertEqual(2, rc)
        self.assertIsNone(bounds)
        self.assertIn("'x'", txt)
        self.assertIn("'a'", txt)
        self.assertNotIn("MERGED DEFAULT VIEW", txt)

    def test_comma_coordinates_are_read_rather_than_dropped(self):
        _rc, _txt, bounds = self.run_main(["112,140"])
        h = rimmap.SIZE // 2
        self.assertEqual((112 - h, 140 - h, 112 - h + rimmap.SIZE,
                          140 - h + rimmap.SIZE), bounds)

    # ---- turn 4: --size with no centre printed the tool's own help ---------
    def test_a_size_with_no_centre_uses_the_base_and_draws(self):
        with mock.patch.object(rimmap, "base_centre", return_value=(118, 144)):
            rc, txt, bounds = self.run_main(["--size", "100", "--layers",
                                             "terrain", "--legend"])
        self.assertEqual(0, rc)
        self.assertEqual((68, 94, 168, 194), bounds)
        self.assertIn("using the pinned base at 118,144", txt)
        self.assertIn("== LAYER 1", txt)
        self.assertLess(txt.index("== LAYER 1"), txt.index("== LEGEND"))

    def test_layers_with_no_centre_also_draws(self):
        with mock.patch.object(rimmap, "base_centre", return_value=(118, 144)):
            rc, _txt, bounds = self.run_main(["--layers", "build"])
        self.assertEqual(0, rc)
        self.assertIsNotNone(bounds)

    def test_no_base_to_fall_back_on_is_said_not_guessed(self):
        with mock.patch.object(rimmap, "base_centre", return_value=None):
            rc, txt, bounds = self.run_main(["--size", "100"])
        self.assertEqual(2, rc)
        self.assertIsNone(bounds)
        self.assertIn("no pinned base", txt)

    def test_legend_alone_is_still_the_legend_alone(self):
        rc, txt, bounds = self.run_main(["--legend"])
        self.assertEqual(0, rc)
        self.assertIsNone(bounds)


class HelpTextTests(unittest.TestCase):
    def test_the_docstring_documents_both_rectangle_forms(self):
        self.assertIn("x z WIDTH HEIGHT", rimmap.__doc__)
        self.assertIn("--corner", rimmap.__doc__)
        self.assertIn("--size 96 48", rimmap.__doc__)

    def test_the_docstring_says_the_items_layer_draws_plants(self):
        self.assertIn("DOES DRAW PLANTS", rimmap.__doc__)

    def test_the_docstring_separates_natural_rock_from_a_built_wall(self):
        self.assertIn("NATURAL ROCK IS NEVER THE SAME CHARACTER AS A BUILT"
                      " WALL", rimmap.__doc__)

    def test_the_docstring_names_legend_only(self):
        self.assertIn("--legend-only", rimmap.__doc__)


class ConduitGlyphTests(unittest.TestCase):
    """A conduit is wire. A standing lamp is a load. Drawing both 'e' let two
    turns 'trace an intact chain' to an unpowered turret (2026-09-07 turn 33).
    """

    def test_a_conduit_and_a_lamp_no_longer_share_a_glyph(self):
        conduit = rimmap.build_glyph(thing("PowerConduit",
                                           className="Building_PowerConduit"))
        lamp = rimmap.build_glyph(thing("StandingLamp"))
        self.assertEqual("=", conduit)
        self.assertEqual("e", lamp)
        self.assertNotEqual(conduit, lamp)

    def test_the_key_names_the_conduit_as_neither_source_nor_load(self):
        keys = dict(rimmap.L5KEY)
        self.assertIn("=", keys)
        self.assertIn("CONDUIT", keys["="])

    def test_a_building_on_a_conduit_cell_outranks_the_conduit(self):
        rank = rimmap.BUILD_RANK
        for glyph in ("s", "b", "t", "w", "H", "/"):
            self.assertLess(rank.get(glyph, 1), rank["="], glyph)
        self.assertLess(rank["!"], rank.get("s", 1))


class LayerNameValidationTests(unittest.TestCase):
    def test_a_roof_area_name_is_refused_and_points_at_the_areas_view(self):
        want, bad, elsewhere = rimmap.parse_layers("roofarea")
        self.assertEqual(set(), want)
        self.assertEqual([], bad)
        self.assertIn("map.py areas", elsewhere["roofarea"])

    def test_roof_still_means_layer_one(self):
        self.assertEqual(({1}, [], {}), rimmap.parse_layers("roof"))

    def test_a_stray_word_after_layers_is_named_not_dropped(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = rimmap.main(["118", "144", "--layers", "roof", "desig"])
        self.assertEqual(2, rc)
        self.assertIn("IGNORED 'desig'", out.getvalue())

    def test_the_cli_refuses_rather_than_drawing_a_different_layer(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = rimmap.main(["118", "144", "--layers", "roofarea"])
        self.assertEqual(2, rc)
        self.assertIn("NOT one of the seven layers", out.getvalue())
        self.assertIn("map.py areas", out.getvalue())


class AreaViewTests(unittest.TestCase):
    def test_the_roof_area_glyph_beats_a_plain_allowed_area(self):
        sym, names = rimmap.area_glyph(
            [{"className": "Area_Allowed", "label": "Home zone"},
             {"className": "Area_BuildRoof", "label": "Build roof"}])
        self.assertEqual("R", sym)
        self.assertEqual(2, len(names))

    def test_no_area_at_all_draws_a_dot(self):
        self.assertEqual(".", rimmap.area_glyph([])[0])

    def test_areas_without_a_cell_refuses_rather_than_guessing(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(2, rimmap.main(["areas"]))
        self.assertIn("needs at least a cell", out.getvalue())

    def test_areas_reads_a_comma_pair_rather_than_dropping_it(self):
        seen = {}

        def fake_scan(x0, z0, x1, z1, **kw):
            seen["bounds"] = (x0, z0, x1, z1)
            return {}

        with mock.patch.object(rimmap.rim, "init"), \
             mock.patch.object(rimmap.rim, "map_size", return_value=(250, 250)), \
             mock.patch.object(rimmap, "scan", side_effect=fake_scan), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            rimmap.main(["areas", "122,140", "6", "6"])
        self.assertEqual((122, 140, 128, 146), seen["bounds"])


class LayerAliasTests(unittest.TestCase):
    """`bldg` and `buildings` are the names a hand types for layer 5."""

    def test_bldg_and_buildings_both_mean_the_build_layer(self):
        for name in ("bldg", "bldgs", "buildings", "building", "build"):
            self.assertEqual(({5}, [], {}), rimmap.parse_layers(name), name)

    def test_every_alias_names_a_real_layer(self):
        for alias, n in rimmap.LAYER_ALIAS.items():
            self.assertIn(n, rimmap.LAYER_NAMES, alias)

    def test_the_valid_name_help_lists_bldg(self):
        self.assertIn("bldg", rimmap.layer_names_help())

    def test_an_unknown_name_comes_back_with_the_valid_ones(self):
        want, bad, elsewhere = rimmap.parse_layers("bldng")
        self.assertEqual(["bldng"], bad)
        self.assertEqual(set(), want)
        self.assertEqual({}, elsewhere)


class PayloadShapeTests(unittest.TestCase):
    """A reply that is not a payload is named where it arrives, never read."""

    def setUp(self):
        self.src = dict(rimmap.SRC)

    def tearDown(self):
        rimmap.SRC.clear()
        rimmap.SRC.update(self.src)

    def test_a_split_stock_reply_raises_instead_of_reading_a_list(self):
        rimmap.SRC.update(plus=False, tool=rimmap.STOCK_TOOL, why="test")
        with mock.patch.object(rimmap.rim, "game",
                               return_value=[{"cells": []}, {"cells": []}]):
            with self.assertRaises(rimmap.rim.BridgeError) as e:
                rimmap.block(0, 0, 4, 4)
        self.assertIn("list", str(e.exception))
        self.assertIn(rimmap.STOCK_TOOL, str(e.exception))

    def test_a_stock_reply_with_no_cells_key_is_not_an_empty_region(self):
        rimmap.SRC.update(plus=False, tool=rimmap.STOCK_TOOL, why="test")
        with mock.patch.object(rimmap.rim, "game",
                               return_value={"success": True}):
            with self.assertRaises(rimmap.rim.BridgeError):
                rimmap.block(0, 0, 4, 4)

    def test_colonists_says_what_it_got_instead_of_an_attribute_error(self):
        out = io.StringIO()
        with mock.patch.object(rimmap.rim, "game", return_value="attn_1 ..."), \
             mock.patch("sys.stdout", out):
            self.assertEqual({}, rimmap.colonists())
        self.assertIn("list_colonists failed", out.getvalue())
        self.assertIn("drawn as unknown", out.getvalue())

    def test_alerts_says_what_it_got_instead_of_an_attribute_error(self):
        with mock.patch.object(rimmap.rim, "game", return_value=["a", "b"]):
            said = rimmap.hostile_alerts()
        self.assertEqual(1, len(said))
        self.assertIn("list_alerts failed", said[0])
        self.assertIn("list", said[0])


class UsageLineTests(unittest.TestCase):
    def test_the_usage_line_states_the_four_number_form(self):
        head = rimmap.__doc__.split("python map.py 112 140")[0]
        self.assertIn("usage: map.py", head)
        self.assertIn("x z WIDTH HEIGHT", head)
        self.assertIn("--corner", head)


if __name__ == "__main__":
    unittest.main()
