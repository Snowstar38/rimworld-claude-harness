import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import rim


class RimCliOutputTests(unittest.TestCase):
    def test_call_out_preserves_payload_beyond_console_preview(self):
        payload = {"text": "x" * 12000}
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "layout.json"
            argv = ["rim.py", "call", "rimworld/get_ui_layout", "{}",
                    "--out", str(out)]
            with patch.object(sys, "argv", argv), \
                 patch.object(rim, "init"), \
                 patch.object(rim, "game", return_value=payload), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout:
                rim.main()
            self.assertEqual(json.dumps(payload, indent=1) + chr(10),
                             out.read_text(encoding="utf-8"))
            self.assertEqual(json.dumps(payload, indent=1)[:8000] + chr(10),
                             stdout.getvalue())

    def test_call_without_out_keeps_console_behavior(self):
        payload = {"ok": True}
        with patch.object(sys, "argv", ["rim.py", "call", "tool", "{}"]), \
             patch.object(rim, "init"), \
             patch.object(rim, "game", return_value=payload), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            rim.main()
        self.assertEqual(json.dumps(payload, indent=1) + chr(10), stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
