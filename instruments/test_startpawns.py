import io
import unittest
from contextlib import redirect_stdout
import startpawns

def sample():
    return {"success": True, "pageOpen": True, "scenario": "Crashlanded", "startingPawnCount": 1,
            "pawnCount": 1, "pawns": [{"index": 0, "selected": True, "name": "Sam",
            "childhood": "Urchin", "adulthood": "Medic", "traits": [{"label": "Kind"}],
            "incapableOf": ["Violent"], "skills": [{"name": "Medical", "level": 9,
            "passion": "Major", "disabled": False}]}], "teamSkills": [{"name": "Medical",
            "level": 9, "passion": "Major", "disabled": False, "pawn": "Sam"}],
            "controls": {"randomize": True, "start": True}}

class StartingPawnsTests(unittest.TestCase):
    def test_render_structured_pawn(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, startpawns.render(sample()))
        out = output.getvalue()
        for expected in ("Sam", "Urchin / Medic", "Kind", "Violent", "Medical 9++",
                         "Randomize: available | Start: available"):
            self.assertIn(expected, out)

    def test_render_refusal(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(1, startpawns.render({"success": False, "error": "wrong page"}))
        self.assertIn("wrong page", output.getvalue())

if __name__ == "__main__":
    unittest.main()
