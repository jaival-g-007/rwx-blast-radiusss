import ast
import difflib
import unittest

from impact import Impact, Source
from patches import read_patch


def patch(old, new):
    return read_patch("".join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), "a/example.py", "b/example.py"
    )))[0]


class ScopeTests(unittest.TestCase):
    def test_local_import_is_distinct_from_signature(self):
        old = "def f(a=1):\n    from math import sin\n    return sin(a)\n"
        new = old.replace("import sin", "import cos as sin")
        impact = Impact.__new__(Impact)
        impact.hits = lambda *args: 1
        impact.broad = lambda *args: 7
        impact.cold, impact.reads, impact.introspection = {}, {}, 0
        source = Source(old)
        for text, expected in [(new, 1), (new.replace("a=1", "a=2"), 7)]:
            changes = list(patch(old, text).edits())
            self.assertEqual(impact.local_header_change("example.py", source, ast.parse(text), changes), expected)

    def test_hash_in_multiline_literal_is_observable(self):
        old = 'text = """\n# old value\n"""\n' + "\n" * 8 + "n = 1\n"
        new = old.replace("old value", "new value").replace("n = 1", "n = 2")
        reduced = patch(old, new).without_trivia().apply(old)
        self.assertNotEqual(ast.dump(ast.parse(reduced)), ast.dump(ast.parse(new)))
