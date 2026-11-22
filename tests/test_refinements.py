import ast
import tempfile
import textwrap
import unittest
from pathlib import Path

from comparisons import Comparisons
from replay import shape


class RefinementTests(unittest.TestCase):
    def test_known_keys_and_uncertain_observations(self):
        observations = Comparisons.__new__(Comparisons)
        observations.complete = 0b01111
        observations.atoms = [("str", "a"), ("str", "b"), ("?",)]
        observations.masks = [0b00011, 0b00110, 0b01000]
        observations.sites = {"p:3:1:0": [(0, 0), (1, 1), (2, 2)]}
        self.assertEqual(observations.lookup_users("p", 3, 1, {"b"}, 31), 30)
        self.assertEqual(observations.lookup_users("p", 4, 1, {"b"}, 31), 31)

    def test_live_probe_only_accepts_restricted_edits(self):
        def parsed(code):
            return shape(ast.parse(code))
        self.assertEqual(parsed("call(1, a > b)"), parsed("call(2, a <= b)"))
        self.assertNotEqual(parsed("call(1)"), parsed("other(1)"))
        self.assertNotEqual(parsed("call(1)"), parsed("call(True)"))
        self.assertNotEqual(parsed("call(1)"), parsed("call(other())"))

    def test_static_index_rejects_an_opaque_escape(self):
        from tools.mapping_uses import consumers
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sqlglot").mkdir()
            (root / "tests").mkdir()
            source = root / "sqlglot/example.py"
            code = textwrap.dedent("""
                class Config:
                    OPTIONS = {"a": 1}
                def decide(c, key):
                    return c.OPTIONS.get(key)
            """)
            source.write_text(code)
            self.assertIn("OPTIONS", consumers(root))
            source.write_text(code + "\ndef other(c):\n    return external(c.OPTIONS)\n")
            self.assertNotIn("OPTIONS", consumers(root))
