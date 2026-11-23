"""C:\\Home\\tools\\ask.py: her answer must survive a cp1252 console."""
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ASK = Path(__file__).resolve().parent.parent.parent / "tools" / "ask.py"
_spec = importlib.util.spec_from_file_location("ask_tool", ASK)
ask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ask)


def cp1252_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")


class AskUnicodeTests(unittest.TestCase):
    def test_an_emoji_answer_still_reaches_stdout(self):
        out = cp1252_stream()
        ask.emit("yes 💙 go ahead", stream=out)
        out.flush()
        text = out.buffer.getvalue().decode("cp1252")
        self.assertIn("yes ", text)
        self.assertIn("go ahead", text)

    def test_emit_never_raises_even_with_a_dead_stream(self):
        broken = mock.Mock()
        broken.write.side_effect = OSError("handle is closed")
        broken.encoding = "cp1252"
        ask.emit("anything", stream=broken)

    def test_main_reconfigures_stdout_to_utf8(self):
        out = cp1252_stream()
        with mock.patch.object(sys, "stdout", out), \
             mock.patch.object(sys, "argv", ["ask.py"]):
            self.assertEqual(1, ask.main())
        self.assertEqual("utf-8", out.encoding)
        self.assertEqual("replace", out.errors)

    def test_tk_safe_drops_only_what_tcl_cannot_take(self):
        self.assertEqual("ok ? here", ask.tk_safe("ok \U0001f600 here"))
        self.assertEqual("curly \u201cquotes\u201d \u2014 fine",
                         ask.tk_safe("curly \u201cquotes\u201d \u2014 fine"))


if __name__ == "__main__":
    unittest.main()
