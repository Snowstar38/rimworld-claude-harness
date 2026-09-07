"""Offline regressions for combined pawn CLI views."""
import io
import unittest
from unittest import mock

import pawns


class PawnsCliTests(unittest.TestCase):
    def test_roster_and_work_share_one_read_and_print_both(self):
        row = {"name": "Ada", "isColonist": True, "dead": False,
               "position": {"x": 1, "z": 2}, "work": {"priorities": []},
               "bio": {"ageBiological": 30}, "thoughts": {"memories": []},
               "equipment": {"armed": False}, "needs": {"food": .8}}
        with mock.patch.object(pawns.sys, "argv", ["pawns.py", "--roster", "--work"]), \
             mock.patch.object(pawns.rim, "init"), \
             mock.patch.object(pawns, "all_pawns", return_value=[row]) as read, \
             mock.patch.object(pawns, "print_roster") as roster, \
             mock.patch.object(pawns, "print_detail") as detail, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, pawns.main())
        read.assert_called_once()
        self.assertTrue(read.call_args.kwargs["work"])
        self.assertTrue(read.call_args.kwargs["bio"])
        self.assertTrue(read.call_args.kwargs["thoughts"])
        self.assertTrue(read.call_args.kwargs["equipment"])
        self.assertTrue(read.call_args.kwargs["needs"])
        roster.assert_called_once_with([row])
        detail.assert_called_once()


class NameFilterTests(unittest.TestCase):
    """BUG 4: `pawns.py Finn --work` printed every colonist."""

    def _row(self, name):
        return {"name": name, "isColonist": True, "dead": False,
                "position": {"x": 1, "z": 2}, "work": {"applies": True},
                "bio": {"ageBiological": 30}, "health": {}, "needs": {},
                "equipment": {"armed": False}, "thoughts": {},
                "schedule": {}, "settings": {}}

    def _run(self, argv, rows):
        buf = io.StringIO()
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + argv),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", return_value=rows) as read,              mock.patch("sys.stdout", buf):
            code = pawns.main()
        return code, read, buf.getvalue()

    def test_bare_name_reaches_every_block(self):
        for flag in ("--work", "--bio", "--health", "--needs", "--gear",
                     "--thoughts", "--schedule", "--settings"):
            code, read, out = self._run(["Finn", flag], [self._row("Finn")])
            self.assertEqual(0, code, flag)
            self.assertEqual("Finn", read.call_args.kwargs.get("nameFilter"), flag)
            self.assertIn("Finn", out)

    def test_bare_name_reaches_the_roster(self):
        code, read, out = self._run(["Finn", "--roster"], [self._row("Finn")])
        self.assertEqual(0, code)
        self.assertEqual("Finn", read.call_args.kwargs.get("nameFilter"))

    def test_dash_name_still_works_and_is_not_read_as_a_value(self):
        code, read, _ = self._run(["--name", "Finn", "--work"], [self._row("Finn")])
        self.assertEqual(0, code)
        self.assertEqual("Finn", read.call_args.kwargs.get("nameFilter"))

    def test_near_value_is_not_mistaken_for_a_name(self):
        code, read, _ = self._run(["--near", "30", "--work"], [self._row("Finn")])
        self.assertEqual(0, code)
        self.assertNotIn("nameFilter", read.call_args.kwargs)

    def test_no_name_still_prints_everyone(self):
        rows = [self._row("Finn"), self._row("Ian")]
        code, read, out = self._run(["--work"], rows)
        self.assertEqual(0, code)
        self.assertNotIn("nameFilter", read.call_args.kwargs)
        self.assertIn("Finn", out)
        self.assertIn("Ian", out)

    def test_a_name_matching_nothing_names_the_colonists_that_exist(self):
        rows = [[], [self._row("Finn"), self._row("Ian")]]
        buf = io.StringIO()
        with mock.patch.object(pawns.sys, "argv", ["pawns.py", "Fnin", "--work"]),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", side_effect=rows),              mock.patch("sys.stdout", buf):
            self.assertEqual(0, pawns.main())
        out = buf.getvalue()
        self.assertIn("no pawn matched 'Fnin'", out)
        self.assertIn("Finn", out)
        self.assertIn("Ian", out)

    def test_a_roster_name_matching_nothing_says_so(self):
        buf = io.StringIO()
        with mock.patch.object(pawns.sys, "argv", ["pawns.py", "Fnin", "--roster"]),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns",
                               side_effect=[[], [self._row("Finn")]]),              mock.patch("sys.stdout", buf):
            self.assertEqual(0, pawns.main())
        out = buf.getvalue()
        self.assertIn("no pawn matched 'Fnin'", out)
        self.assertIn("Finn", out)


class WorkNameTests(unittest.TestCase):
    """BUG 5: `set --work` took defNames but the views show labels."""

    def _set(self, spec, live=None):
        buf = io.StringIO()
        with mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "config",
                               return_value={"success": True, "pawn": {"name": "Ada"}}) as cfg,              mock.patch.object(pawns, "all_pawns",
                               return_value=live or []),              mock.patch("sys.stdout", buf):
            code = pawns.main_set(["Ada", "--work", spec])
        return code, cfg, buf.getvalue()

    def test_labels_resolve_to_defnames(self):
        code, cfg, _ = self._set("Artistic=1,animals=3")
        self.assertEqual(0, code)
        self.assertEqual("Art=1,Handling=3", cfg.call_args.kwargs["work"])

    def test_case_and_spacing_are_ignored(self):
        code, cfg, _ = self._set("bed REST=2,plant cut=4")
        self.assertEqual(0, code)
        self.assertEqual("PatientBedRest=2,PlantCutting=4", cfg.call_args.kwargs["work"])

    def test_a_raw_defname_is_passed_through(self):
        code, cfg, _ = self._set("PlantCutting=0,Cooking=1")
        self.assertEqual(0, code)
        self.assertEqual("PlantCutting=0,Cooking=1", cfg.call_args.kwargs["work"])

    def test_a_bogus_name_is_refused_before_the_write(self):
        code, cfg, out = self._set("Chandlery=1")
        self.assertEqual(1, code)
        cfg.assert_not_called()
        self.assertIn("'Chandlery'", out)
        self.assertIn("PlantCutting", out)      # the valid names are listed

    def test_the_live_work_block_resolves_what_the_table_cannot(self):
        live = [{"work": {"types": [{"name": "Chandlery", "label": "candles"}]}}]
        code, cfg, _ = self._set("candles=2", live=live)
        self.assertEqual(0, code)
        self.assertEqual("Chandlery=2", cfg.call_args.kwargs["work"])

    def test_the_alias_table_maps_to_the_games_own_defnames(self):
        lookup = pawns.work_lookup()
        for label, defname in (("Artistic", "Art"), ("Animals", "Handling"),
                               ("Bed rest", "PatientBedRest"), ("Basic", "BasicWorker"),
                               ("Dark study", "DarkStudy"), ("Plant cut", "PlantCutting")):
            self.assertEqual(defname, lookup[pawns._work_key(label)])
            self.assertEqual(defname, lookup[pawns._work_key(defname)])



class AnimalDistanceTests(unittest.TestCase):
    """turn 13: a horse at 233,156 read as `8 from Longhoff` while Longhoff
    was at 133,154 -- 100 cells. The companion's arithmetic is right (Chebyshev
    over live positions, verified live 2026-09-07), so what was compared were
    two numbers read at different moments. Printing the colonist's own cell
    from the SAME snapshot makes that misread impossible to commit."""

    HORSE = {"name": "Stallion", "defName": "Horse", "animal": True,
             "position": {"x": 233, "z": 156},
             "nearestColonist": "Longhoff", "nearestColonistDistance": 8}

    def test_the_colonists_own_cell_is_printed_next_to_the_distance(self):
        text = pawns.line(self.HORSE, {"Longhoff": (233, 148)})
        self.assertIn("8 from Longhoff@233,148", text)

    def test_arithmetic_that_does_not_hold_says_so_on_the_row(self):
        text = pawns.line(self.HORSE, {"Longhoff": (133, 154)})
        self.assertIn("!!ROW SAYS 8, THE CELLS SAY 100", text)

    def test_with_no_colonist_positions_the_old_line_is_unchanged(self):
        self.assertIn("8 from Longhoff", pawns.line(self.HORSE))
        self.assertNotIn("@", pawns.line(self.HORSE).split("from")[1])

    def test_colonist_positions_skips_rows_with_no_cell(self):
        rows = [{"name": "Ada", "isColonist": True, "position": {"x": 1, "z": 2}},
                {"name": "Bo", "isColonist": True, "position": {}},
                {"name": "Hare", "animal": True, "position": {"x": 9, "z": 9}}]
        self.assertEqual({"Ada": (1, 2)}, pawns.colonist_positions(rows))

    def test_distance_zero_sorts_first_not_last(self):
        # `d or 9999` sent a pawn standing ON a colonist to the bottom.
        on_top = {"nearestColonistDistance": 0}
        far = {"nearestColonistDistance": 90}
        unknown = {"nearestColonistDistance": None}
        self.assertEqual([on_top, far, unknown],
                         sorted([far, unknown, on_top], key=pawns.distance_key))


class DownedAnimalTests(unittest.TestCase):
    """turn 20: a downed timber wolf 3 cells from a colonist could not be
    found in `pawns.py --animals`, and nothing in the output could tell a
    filtered row from an absent one."""

    WOLF = {"name": "Timber wolf", "defName": "Wolf_Timber", "animal": True,
            "wild": True, "downed": True, "position": {"x": 123, "z": 146},
            "nearestColonist": "Longhoff", "nearestColonistDistance": 3,
            "animals": {"applies": True, "wild": True, "thingId": "Wolf1"}}

    def test_a_downed_animal_says_downed_on_its_own_row(self):
        self.assertIn("DOWNED", pawns.line(self.WOLF))
        self.assertIn("alive, not a corpse; wild", pawns.line(self.WOLF))

    def test_downed_is_act_now_and_sorts_above_a_closer_hare(self):
        hare = {"name": "Hare", "animal": True, "nearestColonistDistance": 1}
        self.assertTrue(pawns.act_now(self.WOLF))
        self.assertFalse(pawns.act_now(hare))
        order = sorted([hare, self.WOLF],
                       key=lambda x: (not pawns.act_now(x), pawns.distance_key(x)))
        self.assertIs(self.WOLF, order[0])

    def test_a_hunting_predator_is_act_now_too(self):
        self.assertTrue(pawns.act_now({"job": "PredatorHunt"}))

    def test_the_animal_footer_accounts_for_every_row_the_bridge_sent(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            pawns.print_detail([self.WOLF], {"animals"}, False, False,
                               {"Longhoff": (123, 149)},
                               {"returned": 42, "dead": 1, "notAnimal": 5,
                                "nameFilter": None})
        text = out.getvalue()
        self.assertIn("1 DOWNED (they get back up)", text)
        self.assertIn("bridge returned 42", text)
        self.assertIn("1 dropped as dead", text)
        self.assertIn("5 dropped as not-an-animal", text)
        self.assertIn("1 printed", text)
        self.assertIn("3 from Longhoff@123,149", text)

    def test_the_footer_names_a_bridge_side_name_filter(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            pawns.print_detail([self.WOLF], {"animals"}, True, False, None,
                               {"returned": 1, "dead": 0, "notAnimal": 0,
                                "nameFilter": "wolf"})
        self.assertIn("nameFilter='wolf'", out.getvalue())

    def test_a_name_that_matched_nothing_names_the_animals_too(self):
        rows = [{"name": "Ada", "isColonist": True},
                dict(self.WOLF)]
        out = io.StringIO()
        with mock.patch.object(pawns, "all_pawns", return_value=rows), \
             mock.patch("sys.stdout", out):
            pawns.print_name_miss("timberwolf")
        text = out.getvalue()
        self.assertIn("colonists: Ada", text)
        self.assertIn("Timber wolf/Wolf_Timber DOWNED", text)
        self.assertIn("`Wolf_Timber`", text)      # says why the word missed


class ThreatOrderTests(unittest.TestCase):
    def test_close_rows_come_back_nearest_first_with_zero_included(self):
        rows = [{"name": "far", "nearestColonistDistance": 20},
                {"name": "on top", "nearestColonistDistance": 0},
                {"name": "colonist", "isColonist": True,
                 "nearestColonistDistance": 1}]
        with mock.patch.object(pawns, "all_pawns", return_value=rows):
            _, _, close = pawns.threats(30)
        self.assertEqual(["on top", "far"], [p["name"] for p in close])


class WatchDefaultTests(unittest.TestCase):
    """`pawns.py set --do` froze the picture for ~8 s per write on stream: the
    watch tab is the Work/Assign tab, drawn over the colony."""

    RUNNING = {"state": "running", "speed": "Normal"}
    PAUSED = {"state": "paused", "why": "the play service is not running"}

    def test_a_running_clock_skips_the_watch_and_says_so(self):
        watch, why = pawns.watch_choice(True, state=self.RUNNING)
        self.assertFalse(watch)
        self.assertIn("clock is RUNNING", why)
        self.assertIn("--watch", why)

    def test_a_stopped_clock_still_shows_the_tab(self):
        watch, _ = pawns.watch_choice(True, state=self.PAUSED)
        self.assertTrue(watch)

    def test_the_flags_are_never_second_guessed(self):
        self.assertTrue(pawns.watch_choice(True, on=True, state=self.RUNNING)[0])
        self.assertFalse(pawns.watch_choice(True, off=True, state=self.PAUSED)[0])

    def test_the_write_goes_out_with_watch_false_and_prints_the_reason(self):
        reply = {"success": True, "pawn": {"name": "Ada"},
                 "fields": [{"field": "selfTend", "before": False,
                             "after": True, "changed": True}],
                 "watch": {"shown": False, "reason": "watch:false"}}
        with mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns.clock, "state", return_value=self.RUNNING),              mock.patch.object(pawns, "config", return_value=reply) as config,              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, pawns.main_set(["Ada", "--selftend", "on", "--do"]))
        self.assertIs(False, config.call_args.kwargs["watch"])
        self.assertIn("clock is RUNNING", out.getvalue())


if __name__ == "__main__":
    unittest.main()
