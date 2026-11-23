"""Offline regressions for combined pawn CLI views."""
import io
import json
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
        # "colony", not "colonists": the list carries our ghouls too, and
        # calling it colonists would be the word that lost Ben Cooper.
        self.assertIn("colony: Ada", text)
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


    def test_the_threats_view_keeps_both_ends_of_every_distance(self):
        """`threats()` returns non-colony rows only, so feeding its own output
        to `colonist_positions` always gave {} -- and the !!ROW SAYS / THE CELLS
        SAY guard, the whole reason distances carry both endpoints, could never
        fire in the one view whose job is "is this thing near my colonists"."""
        rows = [{"name": "Longhoff", "isColonist": True,
                 "position": {"x": 133, "z": 154}},
                {"name": "Horse", "defName": "Horse", "thingId": "Horse991",
                 "animal": True, "hostile": True, "mentalState": "manhunter",
                 "position": {"x": 233, "z": 156},
                 "nearestColonist": "Longhoff",
                 "nearestColonistDistance": 8}]
        with mock.patch.object(pawns, "all_pawns", return_value=rows), \
                mock.patch.object(pawns.sys, "argv",
                                  ["pawns.py", "--threats"]), \
                mock.patch.object(pawns.rim, "init"), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.main()
        text = out.getvalue()
        self.assertIn("Longhoff@133,154", text)
        self.assertIn("THE CELLS SAY", text)


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


class FilterFlagTests(unittest.TestCase):
    """Every documented narrowing has to reach the bridge, and the ones this
    file applies have to narrow what is printed. `--jobs` was a flag nobody
    parsed, so the animal census came back looking like an answer."""

    def _row(self, name, **kw):
        row = {"name": name, "isColonist": False, "dead": False, "animal": False,
               "position": {"x": 1, "z": 2}}
        row.update(kw)
        return row

    def _run(self, argv, rows=()):
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + list(argv)),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", return_value=list(rows)) as read,              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main()
        return code, read, out.getvalue()

    def test_every_bridge_narrowing_reaches_the_bridge(self):
        for flag, key in pawns.FILTER_FLAGS:
            code, read, text = self._run([flag], [self._row("x")])
            self.assertEqual(0, code, "%s: %s" % (flag, text))
            self.assertIs(True, read.call_args.kwargs.get(key), flag)
            self.assertIn("bridge filters: %s" % flag, text)

    def test_hostile_and_near_reach_the_bridge_too(self):
        _, read, _ = self._run(["--hostile"], [self._row("x")])
        self.assertIs(True, read.call_args.kwargs.get("hostileOnly"))
        _, read, _ = self._run(["--near", "20"], [self._row("x")])
        self.assertEqual(20, read.call_args.kwargs.get("withinOfColonists"))

    def test_every_block_flag_asks_for_its_block(self):
        for flag, block in (("--health", "health"), ("--needs", "needs"),
                            ("--gear", "equipment"), ("--bio", "bio"),
                            ("--thoughts", "thoughts"), ("--work", "work"),
                            ("--schedule", "schedule"), ("--settings", "settings"),
                            ("--relations", "relations"), ("--animals", "animals")):
            _, read, _ = self._run([flag], [self._row("Ada", isColonist=True,
                                                      animal=True)])
            self.assertIs(True, read.call_args.kwargs.get(block), flag)

    def test_animals_prints_animals_and_nothing_else(self):
        rows = [self._row("Ada", isColonist=True, humanlike=True),
                self._row("Muffalo", animal=True, defName="Muffalo",
                          animals={"applies": True, "tame": True,
                                   "thingId": "Muffalo470153"})]
        _, _, text = self._run(["--animals"], rows)
        self.assertIn("Muffalo", text)
        self.assertNotIn("Ada", text)
        self.assertIn("1 dropped as not-an-animal", text)


class AnimalIdTests(unittest.TestCase):
    """Five muffalo share a name, so the id is the only address a write takes.
    It rides on the NAME line: one read, one copy."""

    def _rows(self):
        return [{"name": "Muffalo", "defName": "Muffalo", "isColonist": False,
                 "dead": False, "animal": True, "position": {"x": 1, "z": 2},
                 "animals": {"applies": True, "tame": True,
                             "thingId": "Muffalo470153", "ageYears": 3,
                             "gender": "Female"},
                 "settings": {"master": None}}]

    def test_the_id_is_on_the_same_line_as_the_name(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.print_detail(self._rows(), {"animals"}, True, False)
        for row in out.getvalue().splitlines():
            if "Muffalo470153" in row:
                self.assertIn("Muffalo", row.split("id:")[0])
                return
        self.fail("the id is not printed at all")

    def test_the_id_is_not_repeated_under_the_row(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.print_detail(self._rows(), {"animals"}, True, False)
        self.assertEqual(1, out.getvalue().count("Muffalo470153"))

    def test_an_unread_id_says_so_rather_than_going_missing(self):
        rows = self._rows()
        rows[0]["animals"].pop("thingId")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.print_detail(rows, {"animals"}, True, False)
        self.assertIn("id:?", out.getvalue())

    def test_the_settings_block_carries_the_same_id(self):
        rows = self._rows()
        rows[0]["animals"].pop("thingId")
        rows[0]["settings"]["thingId"] = "Muffalo470153"
        self.assertEqual("Muffalo470153", pawns.animal_id(rows[0]))


class UnknownFlagTests(unittest.TestCase):
    def _run(self, argv):
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + argv),              mock.patch.object(pawns.rim, "init") as init,              mock.patch.object(pawns, "all_pawns", return_value=[]) as read,              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main()
        return code, out.getvalue(), init, read

    def test_jobs_is_refused_and_names_the_work_tab(self):
        code, text, init, read = self._run(["--jobs"])
        self.assertEqual(1, code)
        self.assertIn("no such flag --jobs", text)
        self.assertIn("--work", text)
        init.assert_not_called()
        read.assert_not_called()

    def test_a_typo_names_the_nearest_real_flag(self):
        code, text, _, _ = self._run(["--animal"])
        self.assertEqual(1, code)
        self.assertIn("--animals", text)

    def test_every_documented_flag_is_accepted(self):
        for flag in pawns.READ_FLAGS:
            self.assertEqual([], pawns.unknown_flags([flag], pawns.READ_FLAGS,
                                                     pawns.VALUE_FLAGS), flag)

    def test_a_value_after_a_value_flag_is_never_a_flag(self):
        self.assertEqual([], pawns.unknown_flags(["--name", "-Finn", "--near", "30"],
                                                 pawns.READ_FLAGS, pawns.VALUE_FLAGS))

    def test_the_write_side_refuses_a_flag_it_cannot_apply(self):
        with mock.patch.object(pawns.rim, "init") as init,              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main_set(["Ada", "--jobs", "Cooking=1", "--do"])
        self.assertEqual(1, code)
        self.assertIn("no such flag --jobs", out.getvalue())
        init.assert_not_called()


class SkillLevelTests(unittest.TestCase):
    """Threadneedle, 2026-09-08, turns 12-17: the game refused a job saying
    "Construction skill too low" and NO instrument could turn that into a
    number. `--roster` printed only passioned skills above level 5 and `--json`
    carried no skills at all. It cost two turns."""

    SKILLS = ("Shooting", "Melee", "Construction", "Mining", "Cooking",
              "Plants", "Animals", "Crafting", "Artistic", "Medicine",
              "Social", "Intellectual")

    def _bio(self, levels=None, passions=None, disabled=()):
        levels = levels or {}
        passions = passions or {}
        return {"ageBiological": 30, "hasSkills": True, "skillCount": 12,
                "incapableOf": list(disabled),
                "skills": [{"name": n, "label": n.lower(), "present": True,
                            "level": levels.get(n, 4),
                            "levelStored": levels.get(n, 4),
                            "passion": passions.get(n, "None"),
                            "disabled": n in disabled}
                           for n in self.SKILLS]}

    def _row(self, name="Ada", **kw):
        # Every block filled: an empty one draws a `!!  NOT REPORTED` line,
        # which is deliberately long and is not what the width test is about.
        return {"name": name, "isColonist": True, "dead": False, "animal": False,
                "position": {"x": 1, "z": 2}, "bio": self._bio(**kw),
                "thoughts": {"memories": []},
                "equipment": {"armed": False, "primaryLabel": "unarmed"},
                "needs": {"food": 0.8, "rest": 0.5, "joy": 0.3, "mood": 0.7}}

    def _run(self, argv, rows):
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + list(argv)),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", return_value=list(rows)) as read,              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main()
        return code, read, out.getvalue()

    # ------------------------------------------------------------- the roster
    def test_the_roster_prints_every_skill_with_its_level(self):
        code, _, text = self._run(["--roster"],
                                  [self._row(levels={"Construction": 2})])
        self.assertEqual(0, code)
        for name in self.SKILLS:
            self.assertIn(name, text, name)
        # The number that was unreachable: a level 2 Construction, below the old
        # floor of 5 and with no passion, so the old roster printed neither.
        self.assertIn("Construction 2", text)

    def test_a_passion_is_still_visually_distinct_on_the_roster(self):
        rows = [self._row(levels={"Melee": 7, "Mining": 7, "Cooking": 7},
                          passions={"Melee": "Minor", "Mining": "Major"})]
        _, _, text = self._run(["--roster"], rows)
        self.assertIn("Melee 7+", text)
        self.assertIn("Mining 7++", text)
        self.assertNotIn("Cooking 7+", text)

    def test_the_roster_keeps_its_lines_short_enough_to_read_aloud(self):
        _, _, text = self._run(["--roster"], [self._row()])
        for row in text.splitlines():
            self.assertLessEqual(len(row), 80, row)

    def test_a_skill_the_pawn_can_never_do_is_a_dash_not_a_zero(self):
        _, _, text = self._run(["--roster"], [self._row(disabled=("Shooting",))])
        self.assertIn("Shooting -", text)
        self.assertNotIn("Shooting 0", text)

    def test_an_unread_level_is_a_question_mark_not_a_zero(self):
        row = self._row()
        row["bio"]["skills"][0]["level"] = None
        _, _, text = self._run(["--roster"], [row])
        self.assertIn("Shooting ?", text)

    def test_the_roster_prints_the_key_for_the_marks(self):
        _, _, text = self._run(["--roster"], [self._row()])
        self.assertIn("skill key:", text)

    # --------------------------------------------------------------- --skills
    def test_skills_asks_for_bio_and_never_for_a_skills_block(self):
        code, read, _ = self._run(["--skills"], [self._row()])
        self.assertEqual(0, code)
        self.assertIs(True, read.call_args.kwargs.get("bio"))
        # `{skills:true}` is not an argument home/list_pawns declares; sending
        # it lands in unknownArguments and narrows nothing.
        self.assertNotIn("skills", read.call_args.kwargs)

    def test_skills_prints_a_row_per_skill_per_colonist(self):
        rows = [self._row("Ada", levels={"Construction": 12}),
                self._row("Cid", levels={"Construction": 3})]
        _, _, text = self._run(["--skills"], rows)
        self.assertIn("Ada", text)
        self.assertIn("Cid", text)
        for name in self.SKILLS:
            self.assertEqual(3, text.count(name), name)   # two tables + best:

    def test_skills_names_who_is_highest_at_each_skill(self):
        rows = [self._row("Ada", levels={"Construction": 12}),
                self._row("Cid", levels={"Construction": 3})]
        _, _, text = self._run(["--skills"], rows)
        self.assertIn("best:", text)
        self.assertIn("Construction Ada 12", text)

    def test_a_skill_nobody_can_do_says_nobody_rather_than_going_missing(self):
        rows = [self._row("Ada", disabled=("Artistic",)),
                self._row("Cid", disabled=("Artistic",))]
        _, _, text = self._run(["--skills"], rows)
        self.assertIn("Artistic nobody", text)

    def test_a_bare_name_narrows_skills_through_the_bridge(self):
        code, read, _ = self._run(["--skills", "Ada"], [self._row("Ada")])
        self.assertEqual(0, code)
        self.assertEqual("Ada", read.call_args.kwargs.get("nameFilter"))

    def test_skills_keeps_its_lines_short_enough_to_read_aloud(self):
        rows = [self._row("Ada"), self._row("Cid"), self._row("Ernst")]
        _, _, text = self._run(["--skills"], rows)
        for row in text.splitlines():
            self.assertLessEqual(len(row), 80, row)

    def test_a_pawn_with_no_tracker_says_so_rather_than_printing_nothing(self):
        row = {"name": "Muffalo", "isColonist": True, "dead": False,
               "animal": True, "position": {"x": 1, "z": 2},
               "bio": {"hasSkills": False, "skills": []}}
        _, _, text = self._run(["--skills"], [row])
        self.assertIn("no skill tracker", text)

    def test_skills_is_a_flag_the_tool_accepts(self):
        self.assertIn("--skills", pawns.READ_FLAGS)
        self.assertEqual([], pawns.unknown_flags(["--skills"], pawns.READ_FLAGS,
                                                 pawns.VALUE_FLAGS))

    # ----------------------------------------------------------------- --json
    def test_json_carries_the_skills(self):
        _, read, text = self._run(["--json"],
                                  [self._row(levels={"Construction": 2})])
        self.assertIs(True, read.call_args.kwargs.get("bio"))
        skills = json.loads(text)["pawns"][0]["bio"]["skills"]
        self.assertEqual(2, {s["name"]: s["level"] for s in skills}["Construction"])
        self.assertEqual(12, len(skills))


class JsonShapeTests(unittest.TestCase):
    """`pawns.py --json` returned a BARE LIST with every animal on the map in
    it: 60 entries to find 3 colonists, no tick, no count, and nothing saying
    what had been filtered (BUGS, Threadneedle 2026-09-08)."""

    def _colonist(self, name="Ada"):
        return {"name": name, "isColonist": True, "dead": False, "animal": False,
                "position": {"x": 1, "z": 2}, "bio": {"skills": []}}

    def _animal(self, name="Hare", **kw):
        row = {"name": name, "defName": name, "isColonist": False, "animal": True,
               "wild": True, "dead": False, "position": {"x": 9, "z": 9},
               "nearestColonistDistance": 40}
        row.update(kw)
        return row

    def _run(self, argv, rows):
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + list(argv)),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", return_value=list(rows)),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main()
        return code, out.getvalue()

    def test_json_is_an_object_with_tick_filters_count_and_pawns(self):
        code, text = self._run(["--json"], [self._colonist()])
        self.assertEqual(0, code)
        payload = json.loads(text)
        self.assertIsInstance(payload, dict)
        for key in ("tick", "filters", "count", "pawns"):
            self.assertIn(key, payload, key)
        self.assertEqual(1, payload["count"])
        self.assertEqual(len(payload["pawns"]), payload["count"])

    def test_the_count_is_the_length_of_the_list_it_counts(self):
        rows = [self._colonist("Ada"), self._colonist("Cid")] + [self._animal()] * 57
        _, text = self._run(["--json"], rows)
        payload = json.loads(text)
        self.assertEqual(2, payload["count"])
        self.assertEqual(57, payload["omittedAnimals"])

    def test_the_default_set_drops_the_grazing_animals(self):
        rows = [self._colonist()] + [self._animal()] * 57
        _, text = self._run(["--json"], rows)
        payload = json.loads(text)
        self.assertEqual(["Ada"], [p["name"] for p in payload["pawns"]])
        self.assertTrue(any("animals dropped" in f for f in payload["filters"]))

    def test_a_hostile_animal_is_never_dropped(self):
        rows = [self._colonist(),
                self._animal("Timber wolf", hostile=True,
                             hostileReason="manhunter")]
        _, text = self._run(["--json"], rows)
        self.assertIn("Timber wolf",
                      [p["name"] for p in json.loads(text)["pawns"]])

    def test_a_hunting_predator_is_never_dropped(self):
        rows = [self._colonist(), self._animal("Cougar", job="PredatorHunt")]
        _, text = self._run(["--json"], rows)
        self.assertIn("Cougar", [p["name"] for p in json.loads(text)["pawns"]])

    def test_a_downed_animal_is_never_dropped(self):
        rows = [self._colonist(), self._animal("Hare", downed=True)]
        _, text = self._run(["--json"], rows)
        self.assertIn("Hare", [p["name"] for p in json.loads(text)["pawns"]])

    def test_every_animal_flag_brings_the_animals_back(self):
        for flag in pawns.JSON_ANIMAL_FLAGS:
            rows = [self._colonist()] + [self._animal()] * 3
            _, text = self._run([flag, "--json"], rows)
            payload = json.loads(text)
            self.assertEqual(0, payload["omittedAnimals"], flag)
            self.assertIn("Hare", [p["name"] for p in payload["pawns"]], flag)

    def test_a_name_search_keeps_the_animals_it_asked_about(self):
        _, text = self._run(["wolf", "--json"], [self._animal("Timber wolf")])
        payload = json.loads(text)
        self.assertEqual(1, payload["count"])
        self.assertEqual(0, payload["omittedAnimals"])

    def test_the_tick_comes_from_the_reply_not_from_a_second_call(self):
        pawns.LAST_REPLY = {"ticksGame": 4242, "pawns": []}
        try:
            self.assertEqual(4242, pawns.last_tick())
        finally:
            pawns.LAST_REPLY = None

    def test_an_older_build_reports_no_tick_rather_than_tick_zero(self):
        _, text = self._run(["--json"], [self._colonist()])
        self.assertIsNone(json.loads(text)["tick"])


class GhoulTests(unittest.TestCase):
    """The chronicle: the colony's ghoul Ben Cooper "never appears in
    `pawns.py --roster`". RimWorld's own `Pawn.IsColonist` ends in
    `!IsSubhuman` and a ghoul's MutantDef is consideredSubhuman, so every view
    that filtered on `isColonist` dropped him without a word."""

    def _ghoul(self, name="Ben Cooper", **kw):
        row = {"name": name, "isColonist": False, "ghoul": True,
               "playerFaction": True, "humanlike": True, "animal": False,
               "dead": False, "position": {"x": 3, "z": 4},
               "faction": "Threadneedle",
               "bio": {"hasSkills": False, "skills": []},
               "thoughts": {}, "equipment": {"primaryLabel": "unarmed"},
               "needs": {"food": 0.4}, "health": {}}
        row.update(kw)
        return row

    def _colonist(self, name="Ada"):
        return {"name": name, "isColonist": True, "dead": False, "animal": False,
                "position": {"x": 1, "z": 2}, "faction": "Threadneedle",
                "bio": {"hasSkills": True, "skills": []}, "thoughts": {},
                "equipment": {}, "needs": {}, "health": {}}

    def _run(self, argv, rows):
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + list(argv)),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", return_value=list(rows)),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main()
        return code, out.getvalue()

    def test_the_predicate_is_is_colonist_or_our_ghoul(self):
        self.assertTrue(pawns.is_colony_member({"isColonist": True}))
        self.assertTrue(pawns.is_colony_member(self._ghoul()))
        self.assertFalse(pawns.is_colony_member({"isColonist": False}))

    def test_somebody_elses_ghoul_is_not_one_of_ours(self):
        theirs = self._ghoul("Ritual ghoul", playerFaction=False, hostile=True)
        self.assertFalse(pawns.is_colony_member(theirs))
        self.assertFalse(pawns.is_ghoul(theirs))

    def test_an_older_build_with_no_ghoul_field_behaves_as_before(self):
        # Neither field present: the predicate collapses to isColonist, which is
        # exactly what it replaced -- a degrade, not an error.
        self.assertTrue(pawns.is_colony_member({"isColonist": True}))
        self.assertFalse(pawns.is_colony_member({"isColonist": False,
                                                 "animal": False}))

    def test_the_ghoul_is_in_the_roster(self):
        code, text = self._run(["--roster"], [self._colonist(), self._ghoul()])
        self.assertEqual(0, code)
        self.assertIn("Ben Cooper", text)

    def test_the_roster_marks_the_ghoul_as_a_ghoul(self):
        _, text = self._run(["--roster"], [self._colonist(), self._ghoul()])
        for row in text.splitlines():
            if row.startswith("Ben Cooper"):
                self.assertIn("ghoul", row)
                break
        else:
            self.fail("no name line for the ghoul")

    def test_the_roster_header_counts_the_ghoul_apart_from_the_colonists(self):
        _, text = self._run(["--roster"], [self._colonist(), self._ghoul()])
        self.assertIn("1 colonist(s) + 1 ghoul", text)

    def test_a_colony_question_reaches_the_ghoul(self):
        # --health narrows to living colony pawns; a ghoul that needs tending is
        # exactly the row that narrowing exists to surface.
        _, text = self._run(["--health"], [self._colonist(), self._ghoul()])
        self.assertIn("Ben Cooper", text)

    def test_the_map_line_tags_the_ghoul(self):
        _, text = self._run([], [self._ghoul()])
        self.assertIn("GHOUL", text)
        self.assertIn("OURS", text)

    def test_the_ghoul_is_not_reported_as_a_nearby_threat(self):
        rows = [self._colonist(),
                self._ghoul(nearestColonistDistance=2, hostile=False)]
        with mock.patch.object(pawns, "all_pawns", return_value=rows):
            _, _, close = pawns.threats(30)
        self.assertEqual([], [p["name"] for p in close])

    def test_a_name_miss_names_the_ghoul_among_who_does_exist(self):
        rows = [[], [self._colonist(), self._ghoul()]]
        with mock.patch.object(pawns.sys, "argv", ["pawns.py", "Fnin", "--roster"]),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", side_effect=rows),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, pawns.main())
        self.assertIn("Ben Cooper (ghoul)", out.getvalue())


class ThingIdOnEveryRowTests(unittest.TestCase):
    """Live, 2026-09-11: `pawns.py --json` and `--wild --json` rows carried NO
    id, and the `--wild` / `--tame` text views printed none either, because the
    ThingID only ever arrived inside `settings{}` / `animals{}`. Five ibex share
    the name "Ibex"; a listing with no id names a thing nothing can be written
    to, and `act.py hunt <id>` had no id to be given."""

    def _animal(self, name="Ibex", tid="Ibex404123", **kw):
        row = {"name": name, "defName": name, "kindDef": name, "thingId": tid,
               "isColonist": False, "animal": True, "wild": True, "dead": False,
               "position": {"x": 49, "z": 96}, "nearestColonistDistance": 12,
               "faction": None}
        row.update(kw)
        return row

    def _colonist(self):
        return {"name": "Ada", "defName": "Human", "thingId": "Human618",
                "isColonist": True, "animal": False, "dead": False,
                "position": {"x": 1, "z": 2}, "faction": "Threadneedle"}

    def _run(self, argv, rows):
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + list(argv)),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns",
                               return_value=[dict(r) for r in rows]),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main()
        return code, out.getvalue()

    def test_the_row_field_is_the_first_place_the_id_is_looked_for(self):
        self.assertEqual("Ibex404123", pawns.animal_id({"thingId": "Ibex404123"}))

    def test_the_blocks_are_still_read_for_an_older_companion(self):
        self.assertEqual("Ibex404123",
                         pawns.animal_id({"animals": {"thingId": "Ibex404123"}}))
        self.assertEqual("Ibex404123",
                         pawns.animal_id({"settings": {"thingId": "Ibex404123"}}))

    def test_every_view_prints_the_id_on_an_animals_name_line(self):
        for argv in ([], ["--wild"], ["--tame"], ["--animals"]):
            _, text = self._run(argv, [self._colonist(), self._animal()])
            self.assertIn("id:Ibex404123", text, argv)

    def test_the_id_is_still_printed_exactly_once(self):
        _, text = self._run(["--animals"], [self._animal()])
        self.assertEqual(1, text.count("Ibex404123"))

    def test_an_unread_id_is_a_question_mark_not_a_missing_column(self):
        row = self._animal()
        row.pop("thingId")
        _, text = self._run(["--wild"], [row])
        self.assertIn("id:?", text)

    def test_a_colonist_row_is_not_given_an_id_column(self):
        _, text = self._run([], [self._colonist()])
        self.assertNotIn("id:", text)

    def test_json_carries_thing_id_at_the_top_level_of_every_row(self):
        _, text = self._run(["--wild", "--json"],
                            [self._colonist(), self._animal()])
        rows = json.loads(text)["pawns"]
        self.assertEqual({"Human618", "Ibex404123"},
                         {p["thingId"] for p in rows})

    def test_json_hoists_the_id_out_of_a_block_for_an_older_companion(self):
        row = self._animal()
        row.pop("thingId")
        row["settings"] = {"thingId": "Ibex404123"}
        _, text = self._run(["--wild", "--json"], [row])
        self.assertEqual("Ibex404123", json.loads(text)["pawns"][0]["thingId"])

    def test_a_row_with_no_id_anywhere_is_counted_and_named(self):
        row = self._animal()
        row.pop("thingId")
        _, text = self._run(["--wild", "--json"], [row])
        payload = json.loads(text)
        self.assertIsNone(payload["pawns"][0]["thingId"])
        self.assertEqual(1, payload["rowsWithNoId"])
        self.assertIn("Rebuild", payload["rowsWithNoIdNote"])

    def test_nothing_claims_a_missing_id_when_every_row_has_one(self):
        _, text = self._run(["--wild", "--json"], [self._animal()])
        self.assertNotIn("rowsWithNoId", json.loads(text))


class ThingIdAsANameTests(unittest.TestCase):
    """Live, 2026-09-11: `pawns.py Ibex45901 --animals` printed "no pawn
    matched". `nameFilter` is a SUBSTRING of the name, the defName and the
    kindDef, and a ThingID is none of those -- so the one unambiguous handle the
    caller had was the one thing the tool would not take."""

    def _animal(self, tid="Ibex45901", x=49):
        return {"name": "Ibex", "defName": "Ibex", "kindDef": "Ibex",
                "thingId": tid, "isColonist": False, "animal": True,
                "wild": True, "dead": False, "position": {"x": x, "z": 96},
                "nearestColonistDistance": 12, "faction": None,
                "animals": {"applies": True, "wild": True, "thingId": tid},
                "settings": {"thingId": tid}}

    def _run(self, argv, rows):
        with mock.patch.object(pawns.sys, "argv", ["pawns.py"] + list(argv)),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns",
                               return_value=[dict(r) for r in rows]) as read,              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = pawns.main()
        return code, read, out.getvalue()

    def test_what_counts_as_an_address_and_what_stays_a_name(self):
        for token in ("Ibex404123", "Thing_Ibex404123", "Wolf_Timber334862"):
            self.assertTrue(pawns.looks_like_thing_id(token), token)
        for token in ("Ibex", "R2", "wolf", "", None, "Ada", "Thing_"):
            self.assertFalse(pawns.looks_like_thing_id(token), token)

    def test_the_bridge_is_asked_for_the_defname_half_not_the_id(self):
        # The prefix can only WIDEN -- the pawn whose id starts with it always
        # matches -- so narrowing stays server-side with nothing at risk.
        self.assertEqual("Ibex", pawns.thing_id_prefix("Ibex45901"))
        self.assertEqual("Wolf_Timber",
                         pawns.thing_id_prefix("Thing_Wolf_Timber334862"))
        _, read, _ = self._run(["Ibex45901", "--animals"], [self._animal()])
        self.assertEqual("Ibex", read.call_args.kwargs.get("nameFilter"))

    def test_an_id_positional_finds_exactly_that_animal(self):
        rows = [self._animal("Ibex45901", 49), self._animal("Ibex45902", 50)]
        code, _, text = self._run(["Ibex45901", "--animals"], rows)
        self.assertEqual(0, code)
        self.assertIn("id:Ibex45901", text)
        self.assertNotIn("Ibex45902", text)
        self.assertIn("matched by id, not by name", text)

    def test_both_spellings_of_one_id_resolve(self):
        rows = [self._animal("Ibex45901", 49), self._animal("Ibex45902", 50)]
        for token in ("Ibex45902", "Thing_Ibex45902"):
            _, _, text = self._run([token, "--animals"], rows)
            self.assertIn("id:Ibex45902", text, token)
            self.assertNotIn("Ibex45901", text, token)

    def test_an_id_reaches_the_json_view_too(self):
        rows = [self._animal("Ibex45901", 49), self._animal("Ibex45902", 50)]
        _, _, text = self._run(["Ibex45901", "--json"], rows)
        payload = json.loads(text)
        self.assertEqual(1, payload["count"])
        self.assertEqual("Ibex45901", payload["pawns"][0]["thingId"])
        self.assertTrue(any("ThingID" in f for f in payload["filters"]))

    def test_a_dead_address_says_the_address_is_dead_not_that_a_name_missed(self):
        rows = [[self._animal("Ibex45901")], [self._animal("Ibex45901")]]
        with mock.patch.object(pawns.sys, "argv",
                               ["pawns.py", "Ibex99999", "--animals"]),              mock.patch.object(pawns.rim, "init"),              mock.patch.object(pawns, "all_pawns", side_effect=rows),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, pawns.main())
        text = out.getvalue()
        self.assertIn("ThingID 'Ibex99999'", text)
        self.assertIn("dead address", text)
        self.assertIn("Thing_Ibex99999", text)     # the other spelling was tried

    def test_a_plain_name_is_still_a_plain_name(self):
        _, read, text = self._run(["ibex", "--animals"], [self._animal()])
        self.assertEqual("ibex", read.call_args.kwargs.get("nameFilter"))
        self.assertIn("--name 'ibex'", text)


if __name__ == "__main__":
    unittest.main()


class OfflineAuditTests(unittest.TestCase):
    """Branches the 2026-09-11 patches left one step short."""

    def test_a_roster_narrowed_by_a_filter_is_not_called_a_failed_read(self):
        """`--roster --animals` narrows to animals, roster() keeps colony
        members, and the empty result was reported as broken plumbing -- the
        file's own cardinal sin ("a filter is not an empty map") inverted."""
        rows = [{"name": "Ibex", "defName": "Ibex", "thingId": "Ibex404123",
                 "animal": True, "wild": True}]
        with mock.patch.object(pawns.sys, "argv",
                               ["pawns.py", "--roster", "--animals"]), \
                mock.patch.object(pawns.rim, "init"), \
                mock.patch.object(pawns, "all_pawns", return_value=rows), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.main()
        text = out.getvalue()
        self.assertNotIn("failed read", text)
        self.assertIn("filter", text)

    def test_the_dead_address_listing_hands_back_live_addresses(self):
        """print_name_miss is what you land on after being told your ThingID is
        dead and to re-read for a live one. It printed name/defName and no id --
        five ibex share both."""
        rows = [{"name": "Ibex", "defName": "Ibex", "thingId": "Ibex404123",
                 "animal": True}]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.print_name_miss("Ibex99999", rows, as_id=True)
        self.assertIn("Ibex404123", out.getvalue())

    def test_an_empty_skill_list_is_not_reported_as_a_missing_payload(self):
        """bio{} with hasSkills true and no rows WAS reported by the DLL. Saying
        "NOT REPORTED by this build" sends the reader to reinstall a companion
        that is working."""
        p = {"name": "Ada", "isColonist": True,
             "bio": {"hasSkills": True, "skills": []}}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.print_skills([p])
        self.assertNotIn("NOT REPORTED", out.getvalue())

    def test_a_missing_bio_block_is_still_reported_as_missing(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.print_skills([{"name": "Ada", "isColonist": True}])
        self.assertIn("NOT REPORTED", out.getvalue())

    def test_the_best_footer_is_printed_for_a_single_colonist_too(self):
        """PLAYBOOK promises the footer unconditionally; `len(have) > 1` made it
        vanish on a one-colonist colony -- a new start, or `--skills <pawn>`."""
        p = {"name": "Ada", "isColonist": True,
             "bio": {"hasSkills": True,
                     "skills": [{"defName": "Construction", "label": "Construction",
                                 "present": True, "level": 12, "passion": "Minor",
                                 "disabled": False}]}}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            pawns.print_skills([p])
        self.assertIn("best:", out.getvalue())

    def test_a_clock_that_could_not_be_read_is_not_called_stopped(self):
        """clock.state() returns {"state": "unknown"} when the read FAILS. The
        watch then failed open AND told the caller the tab "hides nothing that
        is moving" -- stated as measured fact about a read that did not land."""
        watch, why = pawns.watch_choice(True, state={"state": "unknown"})
        self.assertNotIn("hides nothing that is moving", why)
        self.assertIn("could not be read", why)
