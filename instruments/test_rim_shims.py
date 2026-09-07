import unittest
from unittest import mock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import rim


class SetDraftShimTests(unittest.TestCase):
    def call_args(self, args):
        with mock.patch.object(rim, "tool", return_value={"success": True}) as tool:
            rim.game("rimworld/set_draft", args)
        return tool.call_args.args[1]["arguments"]

    def test_plain_pawn_alias_becomes_name(self):
        self.assertEqual({"pawnName": "Finn", "drafted": False},
                         self.call_args({"pawn": "Finn", "drafted": False}))

    def test_stable_pawn_alias_becomes_id(self):
        self.assertEqual({"pawnId": "Pawn_Finn123", "drafted": True},
                         self.call_args({"pawn": "Pawn_Finn123", "drafted": True}))
        self.assertEqual({"pawnId": "123", "drafted": True},
                         self.call_args({"pawn": 123, "drafted": True}))

    def test_explicit_schema_fields_pass_unchanged(self):
        self.assertEqual({"pawnName": "Finn", "drafted": False},
                         self.call_args({"pawnName": "Finn", "drafted": False}))

    def test_alias_and_explicit_selector_conflict(self):
        with self.assertRaisesRegex(rim.BridgeError, "pass exactly one pawn selector"):
            rim.game("rimworld/set_draft", {"pawn": "Finn", "pawnName": "Finn"})


if __name__ == "__main__":
    unittest.main()
